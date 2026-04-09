// terrain_compute.hlsl
// Vertex-HGT Tier 1 Compute Shader
// Fixed: atomic counters, proper sentinel, DX12 registers

// ─────────────────────────────────────────
// CONSTANTS
// ─────────────────────────────────────────

#define CHUNK_DIM            64u
#define CHUNK_SIZE           (CHUNK_DIM * CHUNK_DIM)          // 4096
#define QUADS_PER_CHUNK      ((CHUNK_DIM - 1u) * (CHUNK_DIM - 1u)) // 3969
#define MAX_INDICES_PER_CHUNK (QUADS_PER_CHUNK * 6u)          // 23814
#define INVALID_INDEX        0xFFFFFFFFu

// ─────────────────────────────────────────
// PUSH CONSTANTS — DX12 register syntax
// ─────────────────────────────────────────

cbuffer PushConstants : register(b0) {
    int2  PlayerChunk;
    int   RenderDistance;
    int   TotalChunks;
    float VertexSpacing;
    int   pad0;
    int   pad1;
    int   pad2;
};

// ─────────────────────────────────────────
// INPUT BUFFERS — DX12 register syntax
// t = read-only (SRV), u = read-write (UAV), b = constant buffer
// ─────────────────────────────────────────

StructuredBuffer<uint>     HeightBuffer     : register(t0);
StructuredBuffer<uint>     VimpBuffer       : register(t1);

struct ChunkSlot {
    int2 RelativeCoord;
    int  LodRing;
    int  ChunkIndex;
};

StructuredBuffer<ChunkSlot> CircularTemplate : register(t2);

// ─────────────────────────────────────────
// OUTPUT BUFFERS
// ─────────────────────────────────────────

RWStructuredBuffer<float4> VertexBuffer     : register(u0);
RWStructuredBuffer<uint>   IndexBuffer      : register(u1);

// FIX 1 + 2: Atomic counter per chunk slot
// One uint per chunk, incremented by CSIndices via InterlockedAdd
// Eliminates Pass 3 loop entirely
RWStructuredBuffer<uint>   IndexCountBuffer : register(u2);

struct DrawArgs {
    uint IndexCount;
    uint InstanceCount;
    uint FirstIndex;
    int  VertexOffset;
    uint FirstInstance;
};

RWStructuredBuffer<DrawArgs> DrawArgsBuffer : register(u3);

// ─────────────────────────────────────────
// HELPERS
// ─────────────────────────────────────────

uint SampleHeight(uint chunkIndex, uint localVertIdx) {
    uint bufferBase = chunkIndex * (CHUNK_SIZE / 2u);
    uint wordIndex  = localVertIdx / 2u;
    uint packed     = HeightBuffer[bufferBase + wordIndex];
    if (localVertIdx % 2u == 0u)
        return  packed        & 0xFFFFu;
    else
        return (packed >> 16u) & 0xFFFFu;
}

uint SampleImportance(uint chunkIndex, uint localVertIdx) {
    uint bufferBase = chunkIndex * (CHUNK_SIZE / 4u);
    uint wordIndex  = localVertIdx / 4u;
    uint byteSlot   = localVertIdx % 4u;
    uint packed     = VimpBuffer[bufferBase + wordIndex];
    return (packed >> (byteSlot * 8u)) & 0xFFu;
}

// ─────────────────────────────────────────
// PASS 0: BUFFER INITIALIZER
// Must run before Pass 1 and Pass 2 on first load
// and whenever chunks change.
// Dispatch: (ceil(TotalChunks / 64), 1, 1)
// ─────────────────────────────────────────

[numthreads(64, 1, 1)]
void CSInit(uint3 id : SV_DispatchThreadID) {
    uint slotIndex = id.x;
    if (slotIndex >= (uint)TotalChunks)
        return;

    // FIX 2: Initialize index buffer to sentinel 0xFFFFFFFF
    // so that any unwritten slot is never mistaken for vertex 0
    uint indexBase = slotIndex * MAX_INDICES_PER_CHUNK;
    for (uint i = 0u; i < MAX_INDICES_PER_CHUNK; i++) {
        IndexBuffer[indexBase + i] = INVALID_INDEX;
    }

    // FIX 1: Zero the atomic counter for this slot
    IndexCountBuffer[slotIndex] = 0u;
}

// ─────────────────────────────────────────
// PASS 1: VERTEX GENERATION
// Dispatch: (8, 8, TotalChunks)
// Thread covers one vertex per invocation
// ─────────────────────────────────────────

[numthreads(8, 8, 1)]
void CSVertices(uint3 id : SV_DispatchThreadID) {
    uint localX    = id.x;
    uint localZ    = id.y;
    uint slotIndex = id.z;

    if (localX >= CHUNK_DIM || localZ >= CHUNK_DIM || slotIndex >= (uint)TotalChunks)
        return;

    uint localVertIdx  = localZ * CHUNK_DIM + localX;
    uint chunkIndex    = (uint)CircularTemplate[slotIndex].ChunkIndex;
    int  lodRing       = CircularTemplate[slotIndex].LodRing;
    int2 relCoord      = CircularTemplate[slotIndex].RelativeCoord;
    uint globalVertIdx = slotIndex * CHUNK_SIZE + localVertIdx;

    uint height     = SampleHeight(chunkIndex, localVertIdx);
    uint importance = SampleImportance(chunkIndex, localVertIdx);

    // Cull vertex if importance is non-zero and within current LOD ring
    bool culled = (importance > 0u) && ((int)importance <= lodRing);
    if (culled) height = 0u;

    float worldX = (float)relCoord.x * (float)(CHUNK_DIM - 1u) * VertexSpacing
                 + (float)localX * VertexSpacing;
    float worldY = (float)height;
    float worldZ = (float)relCoord.y * (float)(CHUNK_DIM - 1u) * VertexSpacing
                 + (float)localZ * VertexSpacing;

    // W: 1.0 = active vertex, 0.0 = culled
    // Height == 0 AND culled covers both the LOD cull and the
    // "no vertex here" rule from your spec
    VertexBuffer[globalVertIdx] = float4(worldX, worldY, worldZ,
                                         culled ? 0.0f : 1.0f);
}

// ─────────────────────────────────────────
// PASS 2: INDEX GENERATION WITH ATOMIC COUNTER
// FIX 1: InterlockedAdd replaces Pass 3 loop entirely
// FIX 2: Sentinel init in Pass 0 means unused slots
//         are never confused with vertex 0
// Dispatch: (9, 7, TotalChunks)
// Each thread covers one quad (top-left corner at qx, qz)
// ─────────────────────────────────────────

[numthreads(9, 7, 1)]
void CSIndices(uint3 id : SV_DispatchThreadID) {
    uint qx        = id.x;
    uint qz        = id.y;
    uint slotIndex = id.z;

    if (qx >= (CHUNK_DIM - 1u) || qz >= (CHUNK_DIM - 1u) || slotIndex >= (uint)TotalChunks)
        return;

    uint vertBase = slotIndex * CHUNK_SIZE;
    uint indexBase = slotIndex * MAX_INDICES_PER_CHUNK;

    // Four corners of this quad
    uint v00 = (qz + 0u) * CHUNK_DIM + (qx + 0u);
    uint v10 = (qz + 0u) * CHUNK_DIM + (qx + 1u);
    uint v01 = (qz + 1u) * CHUNK_DIM + (qx + 0u);
    uint v11 = (qz + 1u) * CHUNK_DIM + (qx + 1u);

    // Read active flags — W > 0.5 means vertex is active
    bool a00 = VertexBuffer[vertBase + v00].w > 0.5f;
    bool a10 = VertexBuffer[vertBase + v10].w > 0.5f;
    bool a01 = VertexBuffer[vertBase + v01].w > 0.5f;
    bool a11 = VertexBuffer[vertBase + v11].w > 0.5f;

    // Triangle 1: v00, v10, v01
    if (a00 && a10 && a01) {
        uint offset;
        // FIX 1: atomically claim 3 consecutive index slots
        InterlockedAdd(IndexCountBuffer[slotIndex], 3u, offset);
        IndexBuffer[indexBase + offset + 0u] = vertBase + v00;
        IndexBuffer[indexBase + offset + 1u] = vertBase + v10;
        IndexBuffer[indexBase + offset + 2u] = vertBase + v01;
    }

    // Triangle 2: v10, v11, v01
    if (a10 && a11 && a01) {
        uint offset;
        InterlockedAdd(IndexCountBuffer[slotIndex], 3u, offset);
        IndexBuffer[indexBase + offset + 0u] = vertBase + v10;
        IndexBuffer[indexBase + offset + 1u] = vertBase + v11;
        IndexBuffer[indexBase + offset + 2u] = vertBase + v01;
    }
}

// ─────────────────────────────────────────
// PASS 3: DRAW ARGS BUILDER
// FIX 1: Just reads the atomic counter — no loop whatsoever
// Dispatch: (ceil(TotalChunks / 64), 1, 1)
// ─────────────────────────────────────────

[numthreads(64, 1, 1)]
void CSDrawArgs(uint3 id : SV_DispatchThreadID) {
    uint slotIndex = id.x;
    if (slotIndex >= (uint)TotalChunks)
        return;

    // FIX 1: Counter was atomically maintained by CSIndices
    // Reading it here is O(1) — a single memory fetch
    uint indexCount = IndexCountBuffer[slotIndex];

    DrawArgsBuffer[slotIndex].IndexCount    = indexCount;
    DrawArgsBuffer[slotIndex].InstanceCount = 1u;
    DrawArgsBuffer[slotIndex].FirstIndex    = slotIndex * MAX_INDICES_PER_CHUNK;
    DrawArgsBuffer[slotIndex].VertexOffset  = 0;
    DrawArgsBuffer[slotIndex].FirstInstance = 0u;
}
