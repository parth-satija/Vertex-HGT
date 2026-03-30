import os
import struct

def read_and_display_hmap(file_path):
    """
    Reads a custom .hmap binary file containing 4096 bytes of uint8 data
    and displays the data, starting a new line after every 64 bytes.

    Args:
        file_path (str): The path to the .hmap binary file.
    """
    try:
        with open(file_path, 'rb') as f:
            # Read 4096 bytes of data
            data = f.read(4096)

            if len(data) != 4096:
                print(f"Error: Expected 4096 bytes, but read {len(data)} bytes from {file_path}.")
                return

            print(f"Displaying data from '{file_path}':\n")
            for i, byte_value in enumerate(data):
                # Print each byte as a number, formatted to take 3 characters (e.g., "  0", " 10", "100")
                print(f"{byte_value:3d}", end="")
                # Start a new line after every 64 bytes
                if (i + 1) % 64 == 0:
                    print()
            print("\nEnd of data.")

    except FileNotFoundError:
        print(f"Error: The file '{file_path}' was not found.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    # Create a dummy .hmap file for demonstration purposes
    dummy_file_name = "Test_folderrrrr/yyy/importance/1.hmap"


    # Call the function with the dummy file
    read_and_display_hmap(dummy_file_name)

    # You can also test with a non-existent file to see error handling
    # read_and_display_hmap("non_existent_file.hmap")
