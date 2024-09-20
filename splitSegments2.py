import os
import math
from music21 import converter, tempo
from fractions import Fraction
import sys
import mido
from mido import MidiFile, MidiTrack, MetaMessage

def get_precise_split_points(musicxml_file, measures_per_segment=4):
    score = converter.parse(musicxml_file)
    measures = list(score.recurse().getElementsByClass('Measure'))
    split_points = [0]
    current_time = 0.0
    default_tempo = 120  # Default tempo in BPM
    current_tempo = default_tempo

    # Recursively search for tempo changes in the score
    tempo_changes = list(score.recurse().getElementsByClass(tempo.MetronomeMark))
    tempo_changes.sort(key=lambda x: x.offset)

    tempo_index = 0

    for i, measure in enumerate(measures):
        # Check if there is a tempo change within the measure
        while tempo_index < len(tempo_changes) and tempo_changes[tempo_index].offset <= measure.offset:
            new_tempo = tempo_changes[tempo_index].getQuarterBPM()
            if new_tempo is not None:
                current_tempo = new_tempo
            tempo_index += 1

        if i % measures_per_segment == 0 and i > 0:
            split_points.append(current_time)

        # Calculate duration of the measure in seconds
        measure_duration_beats = measure.duration.quarterLength
        measure_duration_seconds = (60 / current_tempo) * measure_duration_beats
        current_time += measure_duration_seconds

    # Add the end time of the last measure
    split_points.append(current_time)

    return split_points

def build_tempo_map(midi_file):
    """Builds a tempo map: a list of tuples (tick, tempo, cumulative_time_in_seconds)."""
    tempo_map = []
    cumulative_ticks = 0
    cumulative_time = 0.0
    current_tempo = 500000  # Default tempo in microseconds per quarter note

    for msg in midi_file:
        time_in_ticks = msg.time
        cumulative_ticks += time_in_ticks

        # Calculate time passed
        time_in_seconds = time_in_ticks * current_tempo / midi_file.ticks_per_beat / 1e6
        cumulative_time += time_in_seconds

        if msg.type == 'set_tempo':
            # Append the tempo change to the tempo map
            tempo_map.append((cumulative_ticks, current_tempo, cumulative_time))

            # Update current tempo
            current_tempo = msg.tempo

    # Append the final cumulative time
    tempo_map.append((cumulative_ticks, current_tempo, cumulative_time))
    return tempo_map

def time_to_ticks_with_tempo_map(time_in_seconds, tempo_map, ticks_per_beat):
    """Converts time in seconds to ticks, taking into account tempo changes."""
    cumulative_time = 0.0
    cumulative_ticks = 0
    last_tempo = tempo_map[0][1]

    for i in range(len(tempo_map)):
        tick, tempo, temp_cumulative_time = tempo_map[i]
        if time_in_seconds < temp_cumulative_time:
            # Calculate remaining time
            time_diff = time_in_seconds - cumulative_time
            tick_diff = time_diff * ticks_per_beat * 1e6 / last_tempo
            return int(cumulative_ticks + tick_diff)
        else:
            cumulative_time = temp_cumulative_time
            cumulative_ticks = tick
            last_tempo = tempo

    # If time is beyond the last tempo change, extrapolate
    time_diff = time_in_seconds - cumulative_time
    tick_diff = time_diff * ticks_per_beat * 1e6 / last_tempo
    return int(cumulative_ticks + tick_diff)

def split_midi(input_midi_path, split_times, output_dir):
    # Sort split points and ensure they're unique
    split_times = sorted(set(split_times))

    # Load the original MIDI file
    midi_file = mido.MidiFile(input_midi_path)

    # Build the tempo map
    tempo_map = build_tempo_map(midi_file)

    # Convert split times from seconds to ticks using the tempo map
    ticks_per_beat = midi_file.ticks_per_beat
    split_points_ticks = [time_to_ticks_with_tempo_map(time, tempo_map, ticks_per_beat) for time in split_times]

    # Create output directory if it doesn't exist
    midi_file_name = os.path.splitext(os.path.basename(input_midi_path))[0]
    output_subdir = os.path.join(output_dir, midi_file_name)
    os.makedirs(output_subdir, exist_ok=True)

    num_segments = len(split_points_ticks) - 1

    # For each segment, create a list to hold messages per track
    segments = [ [ [] for _ in midi_file.tracks ] for _ in range(num_segments) ]

    # Process each track
    for track_index, track in enumerate(midi_file.tracks):
        abs_tick = 0
        messages = []
        for msg in track:
            abs_tick += msg.time
            messages.append((abs_tick, msg.copy()))

        # Now, assign messages to segments
        msg_index = 0
        for segment_index in range(num_segments):
            segment_start_tick = split_points_ticks[segment_index]
            segment_end_tick = split_points_ticks[segment_index + 1]
            segment_msgs = segments[segment_index][track_index]

            # Process messages within this segment
            while msg_index < len(messages) and messages[msg_index][0] < segment_end_tick:
                abs_msg_tick, msg = messages[msg_index]
                # Calculate delta time
                if len(segment_msgs) == 0:
                    # First message in the segment
                    delta_time = abs_msg_tick - segment_start_tick
                else:
                    # Subsequent messages
                    delta_time = abs_msg_tick - messages[msg_index - 1][0]
                # Ensure delta time is non-negative
                delta_time = max(int(delta_time), 0)
                msg.time = delta_time
                segment_msgs.append(msg)
                msg_index += 1

    # Save each segment
    for segment_index in range(num_segments):
        segment_midi = MidiFile(ticks_per_beat=midi_file.ticks_per_beat)
        for track_msgs in segments[segment_index]:
            new_track = MidiTrack()
            new_track.extend(track_msgs)
            new_track.append(MetaMessage('end_of_track', time=0))
            segment_midi.tracks.append(new_track)
        segment_number = segment_index + 1
        output_filename = os.path.join(output_subdir, f'segment_{segment_number:03d}.mid')
        segment_midi.save(output_filename)
        print(f"Saved: {output_filename}")


def split_musicxml(musicxml_file, split_points, output_dir):
    score = converter.parse(musicxml_file)
    
    # Check for 2048th notes
    for note_or_rest in score.recurse().notesAndRests:
        if note_or_rest.duration.type == '2048th':
            print(f"Warning: Found 2048th note in {musicxml_file}. Skipping this file.")
            return  # Exit the function without processing

    measures = list(score.recurse().getElementsByClass('Measure'))

    default_tempo = 120  # Default tempo in BPM
    tempo_changes = list(score.recurse().getElementsByClass(tempo.MetronomeMark))
    tempo_changes.sort(key=lambda x: x.offset)

    for i in range(len(split_points) - 1):
        start_time = split_points[i]
        end_time = split_points[i + 1]

        # Find the start and end measures based on cumulative time
        current_time = 0.0
        start_measure = None
        end_measure = None
        current_tempo = default_tempo
        tempo_index = 0

        for measure in measures:
            # Update tempo if there's a tempo change
            while tempo_index < len(tempo_changes) and tempo_changes[tempo_index].offset <= measure.offset:
                new_tempo = tempo_changes[tempo_index].getQuarterBPM()
                if new_tempo is not None:
                    current_tempo = new_tempo
                tempo_index += 1

            measure_duration_beats = measure.duration.quarterLength
            measure_duration_seconds = (60 / current_tempo) * measure_duration_beats

            if start_measure is None and current_time >= start_time:
                start_measure = measure.measureNumber

            if current_time >= end_time:
                end_measure = measure.measureNumber - 1
                break

            current_time += measure_duration_seconds

        if end_measure is None:
            end_measure = measures[-1].measureNumber

        # Extract measures
        excerpt = score.measures(start_measure, end_measure)

        output_file = os.path.join(output_dir, f"segment_{i+1:03d}.mxl")
        try:
            excerpt.write('musicxml', output_file)
        except Exception as e:
            print(f"Error writing segment {i+1}: {str(e)}")
            print("Skipping this segment and continuing with the next one.")
            continue

    print(f"Finished processing {musicxml_file}")

def process_directory(musicxml_dir, midi_dir, output_base_dir, measures_per_segment=4):
    musicxml_output_dir = os.path.join(output_base_dir, "MusicXML_segments")
    midi_output_dir = os.path.join(output_base_dir, "MIDI_segments")
    if not os.path.exists(musicxml_output_dir): os.makedirs(musicxml_output_dir, exist_ok=True)
    if not os.path.exists(midi_output_dir): os.makedirs(midi_output_dir, exist_ok=True)

    musicxml_files = [f for f in os.listdir(musicxml_dir) if f.endswith('.mxl') or f.endswith('.musicxml')]

    for musicxml_file in musicxml_files:
        base_name = os.path.splitext(musicxml_file)[0]
        print(f"Checking {base_name}...")

        musicxml_path = os.path.join(musicxml_dir, musicxml_file)

        # Check both .midi and .mid for MIDI files
        midi_path = os.path.join(midi_dir, base_name + '.midi')
        if not os.path.exists(midi_path):
            midi_path = os.path.join(midi_dir, base_name + '.mid')

        if not os.path.exists(midi_path):
            print(f"Skipping {musicxml_file}: No corresponding MIDI file found.")
            continue

        musicxml_output_subdir = os.path.join(musicxml_output_dir, base_name)
        midi_output_subdir = os.path.join(midi_output_dir, base_name)

        # Check if both output directories already exist
        if os.path.exists(musicxml_output_subdir) and os.path.exists(midi_output_subdir):
            print(f"Skipping {base_name}: Segments already exist.")
            continue

        print(f"Processing {base_name}...")

        os.makedirs(musicxml_output_subdir, exist_ok=True)
        os.makedirs(midi_output_subdir, exist_ok=True)

        try:
            split_points = get_precise_split_points(musicxml_path, measures_per_segment)
            split_musicxml(musicxml_path, split_points, musicxml_output_subdir)
            split_midi(midi_path, split_points, midi_output_subdir)
        except Exception as e:
            print(f"Error processing {base_name}: {str(e)}")
            print(f"Skipping {base_name} and continuing with the next file.")
            continue

        print(f"Finished processing {base_name}")

    print("All files processed.")

# Usage
if len(sys.argv) != 4:
    print("Usage: python splitSegments.py <source_musicxml_directory> <source_midi_directory> <destination_directory>")
    sys.exit(1)

musicxml_directory = sys.argv[1]
midi_directory = sys.argv[2]
segment_directory = sys.argv[3]

process_directory(musicxml_directory, midi_directory, segment_directory)
