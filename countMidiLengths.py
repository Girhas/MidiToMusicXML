import os
import pretty_midi

def get_total_length_of_midi_files(directory):
    """Returns the total length of all MIDI files in a directory."""
    total_length = 0.0
    midi_files = [f for f in os.listdir(directory) if f.endswith('.mid') or f.endswith('.midi')]
    
    for midi_file in midi_files:
        file_path = os.path.join(directory, midi_file)
        midi_data = pretty_midi.PrettyMIDI(file_path)
        total_length += midi_data.get_end_time()
    
    return total_length

def compare_midi_lengths(segments_dir, originals_dir):
    """Compares the total length of MIDI segments to the total length of original MIDI files."""
    total_segment_length = get_total_length_of_midi_files(segments_dir)
    total_original_length = get_total_length_of_midi_files(originals_dir)
    
    print(f"Total length of MIDI segments: {total_segment_length} seconds")
    print(f"Total length of original MIDI files: {total_original_length} seconds")
    
    if abs(total_segment_length - total_original_length) < 1e-3:  # Small tolerance for floating-point errors
        print("The total lengths of the segments and originals match!")
    else:
        print("Warning: The total lengths of the segments and originals do not match.")
        difference = total_original_length - total_segment_length
        print(f"Difference: {difference} seconds")

if __name__ == "__main__":
    segments_directory = input("Enter the directory path to the MIDI segments: ")
    originals_directory = input("Enter the directory path to the original MIDI files: ")
    
    compare_midi_lengths(segments_directory, originals_directory)
