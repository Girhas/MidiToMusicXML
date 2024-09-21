import os
import sys
import mido
from music21 import converter, stream
from mido import MidiFile, MidiTrack, MetaMessage

def build_midi_tempo_map(midi_file):
    """Builds a tempo map from the MIDI file."""
    tempo_map = []
    current_tempo = 500000  # Default tempo (microseconds per beat)
    cumulative_time = 0.0
    cumulative_ticks = 0
    ticks_per_beat = midi_file.ticks_per_beat

    for msg in midi_file:
        delta_time_sec = mido.tick2second(msg.time, ticks_per_beat, current_tempo)
        cumulative_time += delta_time_sec
        cumulative_ticks += msg.time
        if msg.type == 'set_tempo':
            tempo_map.append({
                'time': cumulative_time,
                'tempo': msg.tempo,
                'ticks': cumulative_ticks
            })
            current_tempo = msg.tempo

    return tempo_map

def get_measure_start_times(score, tempo_map):
    """Calculates cumulative times at the start of each measure in the MusicXML file, using the MIDI tempo map."""
    measures = list(score.parts[0].getElementsByClass('Measure'))
    measure_times = []
    current_time = 0.0
    current_tempo = tempo_map[0]['tempo'] if tempo_map else 500000  # Default tempo
    tempo_index = 0

    for measure in measures:
        # Check for tempo changes in the MIDI tempo map
        if tempo_index < len(tempo_map) and current_time >= tempo_map[tempo_index]['time']:
            current_tempo = tempo_map[tempo_index]['tempo']
            tempo_index += 1

        # Calculate the duration of the measure
        measure_duration_beats = measure.barDuration.quarterLength
        seconds_per_beat = current_tempo / 1e6
        measure_duration_sec = measure_duration_beats * seconds_per_beat

        measure_times.append(current_time)
        current_time += measure_duration_sec

    # Append the time at the end of the last measure
    measure_times.append(current_time)

    return measure_times

def calculate_split_points(measure_times, measures_per_segment):
    """Calculates split points based on measure times."""
    split_points = []
    num_measures = len(measure_times) - 1
    for i in range(0, num_measures, measures_per_segment):
        split_points.append(measure_times[i])
    split_points.append(measure_times[-1])  # Ensure the last measure is included
    return split_points

def split_musicxml(musicxml_file, split_points, measures_per_segment, output_dir):
    """Splits the MusicXML file into segments based on measures."""
    score = converter.parse(musicxml_file)
    parts = score.parts
    measures_list = list(parts[0].getElementsByClass('Measure'))
    total_measures = len(measures_list)
    num_segments = (total_measures + measures_per_segment - 1) // measures_per_segment  # Ceiling division

    for seg_num in range(num_segments):
        start_index = seg_num * measures_per_segment
        end_index = min(start_index + measures_per_segment, total_measures)
        segment_score = stream.Score()
        for part in parts:
            part_segment = stream.Part()
            part_segment.id = part.id
            measures = list(part.getElementsByClass('Measure'))
            part_measures = measures[start_index:end_index]
            part_segment.append(part_measures)
            segment_score.append(part_segment)

        # Check if the segment contains any measures
        if len(segment_score.parts[0].getElementsByClass('Measure')) == 0:
            print(f"Warning: No measures found for segment {seg_num + 1}.")
            continue

        output_file = os.path.join(output_dir, f"segment_{seg_num + 1:03d}.mxl")
        try:
            segment_score.write('musicxml', output_file)
            print(f"Saved MusicXML segment: {output_file}")
        except Exception as e:
            print(f"Error writing segment {seg_num + 1}: {str(e)}")
            continue

def split_midi(input_midi_path, split_points, output_dir):
    """Splits the MIDI file into segments based on split times."""
    midi_file = mido.MidiFile(input_midi_path)
    ticks_per_beat = midi_file.ticks_per_beat

    # Build MIDI tempo map
    tempo_map = build_midi_tempo_map(midi_file)

    # Convert split times from seconds to ticks
    split_points_ticks = []
    for time_point in split_points:
        cumulative_ticks = 0
        cumulative_time = 0.0
        current_tempo = 500000  # Default tempo
        for i, tempo_event in enumerate(tempo_map):
            if time_point < tempo_event['time']:
                delta_time_sec = time_point - cumulative_time
                delta_ticks = mido.second2tick(delta_time_sec, ticks_per_beat, current_tempo)
                cumulative_ticks += delta_ticks
                break
            else:
                delta_time_sec = tempo_event['time'] - cumulative_time
                delta_ticks = mido.second2tick(delta_time_sec, ticks_per_beat, current_tempo)
                cumulative_ticks += delta_ticks
                cumulative_time = tempo_event['time']
                current_tempo = tempo_event['tempo']
        else:
            # If time_point is beyond the last tempo change
            delta_time_sec = time_point - cumulative_time
            delta_ticks = mido.second2tick(delta_time_sec, ticks_per_beat, current_tempo)
            cumulative_ticks += delta_ticks

        split_points_ticks.append(int(cumulative_ticks))

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
            while msg_index < len(messages) and messages[msg_index][0] <= segment_end_tick:
                abs_msg_tick, msg = messages[msg_index]
                if abs_msg_tick < segment_start_tick:
                    msg_index += 1
                    continue
                # Calculate delta time
                if len(segment_msgs) == 0:
                    delta_time = abs_msg_tick - segment_start_tick
                else:
                    delta_time = abs_msg_tick - messages[msg_index - 1][0]
                msg.time = max(int(delta_time), 0)
                segment_msgs.append(msg)
                msg_index += 1

    # Save each segment
    for segment_index in range(num_segments):
        segment_midi = MidiFile(ticks_per_beat=midi_file.ticks_per_beat)
        empty_segment = True
        for track_msgs in segments[segment_index]:
            if track_msgs:
                empty_segment = False
            new_track = MidiTrack()
            new_track.extend(track_msgs)
            new_track.append(MetaMessage('end_of_track', time=0))
            segment_midi.tracks.append(new_track)
        if empty_segment:
            print(f"Warning: No MIDI events found for segment {segment_index + 1}. Skipping empty segment.")
            continue
        segment_number = segment_index + 1
        output_filename = os.path.join(output_subdir, f"segment_{segment_number:03d}.mid")
        segment_midi.save(output_filename)
        print(f"Saved MIDI segment: {output_filename}")

def process_directory(musicxml_dir, midi_dir, output_base_dir, measures_per_segment=4):
    """Processes all files in the directories and splits them into segments based on measures."""
    musicxml_output_dir = os.path.join(output_base_dir, "MusicXML_segments")
    midi_output_dir = os.path.join(output_base_dir, "MIDI_segments")
    if not os.path.exists(musicxml_output_dir): os.makedirs(musicxml_output_dir, exist_ok=True)
    if not os.path.exists(midi_output_dir): os.makedirs(midi_output_dir, exist_ok=True)

    musicxml_files = [f for f in os.listdir(musicxml_dir) if f.endswith('.mxl') or f.endswith('.musicxml')]

    for musicxml_file in musicxml_files:
        base_name = os.path.splitext(musicxml_file)[0]
        print(f"Processing {base_name}...")

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

        # Create output directories
        if os.path.exists(musicxml_output_subdir) and os.path.exists(midi_output_subdir): 
            print(f"Skipping {base_name}: Already segmented.")
            continue 
        os.makedirs(musicxml_output_subdir, exist_ok=True)
        os.makedirs(midi_output_subdir, exist_ok=True)

        try:
            # Parse the MusicXML file
            score = converter.parse(musicxml_path)
            num_measures = len(score.parts[0].getElementsByClass('Measure'))
            print(f"Total measures in {base_name}: {num_measures}")

            # Load MIDI file and build tempo map
            midi_file = mido.MidiFile(midi_path)
            midi_tempo_map = build_midi_tempo_map(midi_file)

            # Get measure start times using MIDI tempo map
            measure_times = get_measure_start_times(score, midi_tempo_map)

            # Calculate split points based on measures per segment
            split_points = calculate_split_points(measure_times, measures_per_segment)

            # Split MusicXML
            split_musicxml(musicxml_path, split_points, measures_per_segment, musicxml_output_subdir)

            # Split MIDI
            split_midi(midi_path, split_points, midi_output_subdir)
        except Exception as e:
            print(f"Error processing {base_name}: {str(e)}")
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

# Set the number of measures per segment as desired
measures_per_segment = 4

process_directory(musicxml_directory, midi_directory, segment_directory, measures_per_segment=measures_per_segment)
