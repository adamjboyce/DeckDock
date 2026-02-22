#!/usr/bin/env python3
"""Identify the system of a CHD disc image by reading its metadata.

Designed to run on the NAS (where CHD files live) to avoid transferring
hundreds of MB over the network. Reads the CHD v5 metadata (track type info)
to classify the disc system.

Usage:
    python3 chd-identify.py /path/to/file.chd
    python3 chd-identify.py /path/to/directory/   # scans all .chd files

Output: one line per file: "system_slug\tfilename" or "unknown\tfilename"
"""
import struct
import sys
from pathlib import Path


def read_chd_metadata(filepath):
    """Read all CHT2 track metadata entries from a CHD v5 file.

    Returns list of dicts with keys: track, type, subtype, frames, etc.
    """
    tracks = []
    with open(filepath, "rb") as f:
        header = f.read(124)
        if len(header) < 124 or header[:8] != b"MComprHD":
            return tracks
        version = struct.unpack(">I", header[12:16])[0]
        if version != 5:
            return tracks

        meta_offset = struct.unpack(">Q", header[48:56])[0]

        offset = meta_offset
        for _ in range(100):  # safety limit
            if offset == 0:
                break
            f.seek(offset)
            meta_header = f.read(16)
            if len(meta_header) < 16:
                break

            tag = meta_header[0:4]
            raw_len_flags = struct.unpack(">I", meta_header[4:8])[0]
            data_length = raw_len_flags & 0x00FFFFFF
            next_off = struct.unpack(">Q", meta_header[8:16])[0]

            if tag == b"CHT2":
                data = f.read(min(data_length, 512))
                text = data.decode("ascii", errors="ignore").strip()
                # Parse "TRACK:1 TYPE:MODE2_RAW SUBTYPE:NONE FRAMES:251793 ..."
                fields = {}
                for pair in text.split():
                    if ":" in pair:
                        k, v = pair.split(":", 1)
                        fields[k] = v
                tracks.append(fields)

            offset = next_off

    return tracks


def identify_from_tracks(tracks):
    """Identify system from CHD track metadata.

    Heuristics:
    - Track 1 MODE2_RAW → PSX (XA sector format, definitive)
    - Track 1 MODE1_RAW + logical_bytes > 1GB → PS2 (DVD)
    - Track 1 MODE1_RAW → Saturn, Sega CD, Dreamcast, or PC Engine CD
    - All tracks AUDIO → 3DO (3DO encodes data as audio sectors)
    """
    if not tracks:
        return None

    track1_type = tracks[0].get("TYPE", "")

    # PSX: Mode 2 raw sectors (XA format)
    if track1_type == "MODE2_RAW":
        return "psx"

    # MODE1_RAW: could be Saturn, Sega CD, Dreamcast, PC Engine CD
    # Can't distinguish without reading sector 0 magic bytes
    if track1_type in ("MODE1_RAW", "MODE1"):
        return None  # ambiguous

    # All-AUDIO tracks: characteristic of 3DO discs
    # (3DO, Jaguar CD, and CD-i can all have AUDIO-only layouts,
    # but 3DO is by far the most common in ROM collections)
    all_audio = all(t.get("TYPE") == "AUDIO" for t in tracks)
    if all_audio:
        return "3do"

    return None


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <file.chd | directory>", file=sys.stderr)
        sys.exit(1)

    target = Path(sys.argv[1])
    if target.is_dir():
        files = sorted(target.glob("*.chd"))
    else:
        files = [target]

    for filepath in files:
        try:
            tracks = read_chd_metadata(filepath)
            system = identify_from_tracks(tracks)
            print(f"{system or 'unknown'}\t{filepath.name}")
        except Exception as e:
            print(f"error\t{filepath.name}\t{e}", file=sys.stderr)


if __name__ == "__main__":
    main()
