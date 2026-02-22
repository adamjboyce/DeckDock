#!/bin/bash
# ============================================================================
# DeckDock - NAS Download Hook (sourceable)
# ============================================================================
# Source this at the top of any EmuDeck launcher script to add NAS download
# support. If any argument is a NAS symlink, downloads it locally with a
# zenity progress bar before the emulator launches.
#
# Usage (in any launcher .sh):
#   [ -f "$HOME/Emulation/tools/deckdock-nas-hook.sh" ] && . "$HOME/Emulation/tools/deckdock-nas-hook.sh"
#
# Zero overhead for local files — only activates for NAS symlinks.
# ============================================================================

_DECKDOCK_NAS_MOUNT="/tmp/nas-roms"
_DECKDOCK_NAS_HOST=""
_DECKDOCK_NAS_USER="root"
_DECKDOCK_NAS_EXPORT=""
_DECKDOCK_SSH_KEY="$HOME/.ssh/id_ed25519"
_DECKDOCK_LOCKFILE="/tmp/deckdock-download.lock"
_DECKDOCK_TMP_SUFFIX=".deckdock-tmp"
_DECKDOCK_MIN_FREE_MB=2048

# Raise a zenity window above ES-DE's fullscreen window in gamescope.
# Without this, zenity renders behind ES-DE and the user sees a black screen.
_deckdock_raise_zenity() {
    sleep 0.3
    xdotool search --name "DeckDock" windowactivate windowraise 2>/dev/null || true
}

# Load config
for _cfg in "$HOME/DeckDock/config.env" "$HOME/Emulation/tools/config.env"; do
    if [ -f "$_cfg" ]; then
        eval "$(grep -E '^(NAS_MOUNT|NAS_HOST|NAS_USER|NAS_EXPORT)=' "$_cfg")"
        _DECKDOCK_NAS_MOUNT="${NAS_MOUNT:-$_DECKDOCK_NAS_MOUNT}"
        _DECKDOCK_NAS_HOST="${NAS_HOST:-$_DECKDOCK_NAS_HOST}"
        _DECKDOCK_NAS_USER="${NAS_USER:-$_DECKDOCK_NAS_USER}"
        _DECKDOCK_NAS_EXPORT="${NAS_EXPORT:-$_DECKDOCK_NAS_EXPORT}"
        break
    fi
done

# Quick bail: check if any arg looks like a NAS symlink
_deckdock_needs_download=false
_deckdock_rom_arg=""
for _arg in "$@"; do
    # Use readlink -f to follow multi-level symlink chains (e.g., n3ds/ -> 3ds/ -> NAS)
    if [ -L "$_arg" ] && [[ "$(readlink -f "$_arg" 2>/dev/null)" == "$_DECKDOCK_NAS_MOUNT"/* ]]; then
        _deckdock_needs_download=true
        _deckdock_rom_arg="$_arg"
        break
    fi
done

if [ "$_deckdock_needs_download" = true ]; then
    _nas_target="$(readlink -f "$_deckdock_rom_arg")"
    _rel_path="${_nas_target#$_DECKDOCK_NAS_MOUNT/}"

    # Check if NAS file exists via SSH (bypasses unreliable SSHFS mount checks)
    if ! ssh -i "$_DECKDOCK_SSH_KEY" \
            -o StrictHostKeyChecking=accept-new \
            -o ConnectTimeout=5 \
            "${_DECKDOCK_NAS_USER}@${_DECKDOCK_NAS_HOST}" \
            "test -f \"${_DECKDOCK_NAS_EXPORT}/${_rel_path}\"" 2>/dev/null; then
        _deckdock_raise_zenity &
        zenity --error --title="DeckDock" --text="NAS is not available or this game was removed.\nConnect to your home network and try again." --width=400 2>/dev/null || true
        exit 1
    fi

    # Acquire lock
    exec 9>"$_DECKDOCK_LOCKFILE"
    if ! flock -n 9; then
        _deckdock_raise_zenity &
        zenity --error --title="DeckDock" --text="Another download is in progress.\nPlease wait and try again." --width=400 2>/dev/null || true
        exit 1
    fi

    # Find the original NAS symlink — may differ from $_deckdock_rom_arg if
    # the arg is an alias symlink (e.g., n3ds/ -> 3ds/ -> NAS)
    _original_link="$_deckdock_rom_arg"
    while [ -L "$_original_link" ]; do
        _link_target="$(readlink "$_original_link")"
        # If this level points to NAS, this is the original NAS symlink
        [[ "$_link_target" == "$_DECKDOCK_NAS_MOUNT"/* ]] && break
        # Otherwise follow one more level
        _original_link="$_link_target"
    done
    _rom_dir="$(dirname "$_original_link")"
    _rom_name="$(basename "$_original_link")"
    _nas_dir="$(dirname "$_nas_target")"
    _ext="${_original_link##*.}"
    _ext="${_ext,,}"

    # Resolve companion files (read via SSH to avoid SSHFS flakiness)
    _nas_remote="${_DECKDOCK_NAS_EXPORT}/${_rel_path}"
    _nas_remote_dir="$(dirname "$_nas_remote")"
    _files_list="$_nas_target"
    case "$_ext" in
        m3u)
            while IFS= read -r _line; do
                _line="$(echo "$_line" | sed 's/\r$//')"
                [ -z "$_line" ] && continue
                [[ "$_line" == \#* ]] && continue
                _files_list="$_files_list"$'\n'"$_nas_dir/$_line"
            done < <(ssh -i "$_DECKDOCK_SSH_KEY" -o ConnectTimeout=5 \
                "${_DECKDOCK_NAS_USER}@${_DECKDOCK_NAS_HOST}" \
                "cat \"${_nas_remote}\"" 2>/dev/null)
            ;;
        cue)
            while IFS= read -r _binfile; do
                _files_list="$_files_list"$'\n'"$_nas_dir/$_binfile"
            done < <(ssh -i "$_DECKDOCK_SSH_KEY" -o ConnectTimeout=5 \
                "${_DECKDOCK_NAS_USER}@${_DECKDOCK_NAS_HOST}" \
                "grep -i '^[[:space:]]*FILE' \"${_nas_remote}\"" 2>/dev/null | \
                sed -E 's/^[[:space:]]*FILE[[:space:]]+"?([^"]+)"?.*/\1/')
            ;;
    esac

    # Calculate total size via SSH (SSHFS stat is unreliable)
    _total_bytes=0
    while IFS= read -r _src; do
        [ -z "$_src" ] && continue
        _src_rel="${_src#$_DECKDOCK_NAS_MOUNT/}"
        _sz="$(ssh -i "$_DECKDOCK_SSH_KEY" -o ConnectTimeout=5 \
            "${_DECKDOCK_NAS_USER}@${_DECKDOCK_NAS_HOST}" \
            "stat -c%s \"${_DECKDOCK_NAS_EXPORT}/${_src_rel}\"" 2>/dev/null || echo 0)"
        _total_bytes=$((_total_bytes + _sz))
    done <<< "$_files_list"
    _total_mb=$((_total_bytes / 1024 / 1024))

    # Check disk space
    _free_mb="$(df --output=avail -m "$_rom_dir" | tail -1 | tr -d ' ')"
    if [ "$_free_mb" -lt "$((_total_mb + _DECKDOCK_MIN_FREE_MB))" ]; then
        _deckdock_raise_zenity &
        zenity --error --title="DeckDock" --text="Not enough disk space.\nNeed ~${_total_mb}MB but only ${_free_mb}MB free." --width=400 2>/dev/null || true
        flock -u 9
        exit 1
    fi

    # Clean partial downloads
    find "$_rom_dir" -name "*${_DECKDOCK_TMP_SUFFIX}" -delete 2>/dev/null || true

    # Download each file with progress
    _file_count="$(echo "$_files_list" | wc -l)"
    _current=0
    _failed=false

    while IFS= read -r _src_path; do
        [ -z "$_src_path" ] && continue
        _current=$((_current + 1))
        _filename="$(basename "$_src_path")"
        _dst_path="$_rom_dir/$_filename"
        _tmp_dst="${_dst_path}${_DECKDOCK_TMP_SUFFIX}"
        _label="Downloading ${_filename}"
        [ "$_file_count" -gt 1 ] && _label="Downloading ${_filename} (${_current}/${_file_count})"

        mkdir -p "$(dirname "$_dst_path")"
        # Download via SCP directly over SSH — bypasses NFS entirely.
        # NFS v3 hard mounts stall on large reads, creating unkillable D-state
        # processes. SCP over SSH is reliable and gives us file-size polling.
        _deckdock_fifo="/tmp/deckdock-progress-$$"
        rm -f "$_deckdock_fifo"
        mkfifo "$_deckdock_fifo"
        # Translate mount path to remote path and get size via SSH
        _rel_path="${_src_path#$_DECKDOCK_NAS_MOUNT/}"
        _src_size="$(ssh -i "$_DECKDOCK_SSH_KEY" -o ConnectTimeout=5 \
            "${_DECKDOCK_NAS_USER}@${_DECKDOCK_NAS_HOST}" \
            "stat -c%s \"${_DECKDOCK_NAS_EXPORT}/${_rel_path}\"" 2>/dev/null || echo 0)"
        _scp_src="${_DECKDOCK_NAS_USER}@${_DECKDOCK_NAS_HOST}:${_DECKDOCK_NAS_EXPORT}/${_rel_path}"
        scp -i "$_DECKDOCK_SSH_KEY" \
            -o StrictHostKeyChecking=accept-new \
            -o ConnectTimeout=10 \
            "$_scp_src" "$_tmp_dst" >/dev/null 2>&1 &
        _deckdock_dl_pid=$!
        # Poll file size and feed percentage to zenity via FIFO
        (
            while kill -0 "$_deckdock_dl_pid" 2>/dev/null; do
                _cur_size="$(stat -c%s "$_tmp_dst" 2>/dev/null || echo 0)"
                if [ "$_src_size" -gt 0 ]; then
                    echo $((_cur_size * 100 / _src_size))
                fi
                sleep 1
            done
            echo 100
        ) > "$_deckdock_fifo" &
        _deckdock_poll_pid=$!
        zenity --progress \
            --title="DeckDock - Downloading" \
            --text="$_label" \
            --percentage=0 \
            --no-cancel \
            --auto-close \
            --width=400 < "$_deckdock_fifo" 2>/dev/null &
        _deckdock_zenity_pid=$!
        # Give zenity a moment to create its window, then raise it
        _deckdock_raise_zenity &
        wait $_deckdock_dl_pid 2>/dev/null || true
        wait $_deckdock_poll_pid 2>/dev/null || true
        wait $_deckdock_zenity_pid 2>/dev/null || true
        rm -f "$_deckdock_fifo"

        if [ -f "$_tmp_dst" ]; then
            mv "$_tmp_dst" "$_dst_path"
        else
            _failed=true
            break
        fi
    done <<< "$_files_list"

    flock -u 9

    if [ "$_failed" = true ]; then
        _deckdock_raise_zenity &
        zenity --error --title="DeckDock" --text="Download failed.\nThe game will re-download next time." --width=400 2>/dev/null || true
        find "$_rom_dir" -name "*${_DECKDOCK_TMP_SUFFIX}" -delete 2>/dev/null || true
        exit 1
    fi

    # Replace the original NAS symlink with the downloaded file
    # The copy loop may have already replaced the symlink via mv — only rm if still a link
    _main_filename="$(basename "$_nas_target")"
    [ -L "$_original_link" ] && rm -f "$_original_link"
    if [ "$_rom_name" != "$_main_filename" ] && [ -f "$_rom_dir/$_main_filename" ]; then
        mv "$_rom_dir/$_main_filename" "$_original_link"
    fi

    # Background: update Steam shortcuts, fetch artwork, then wait for the
    # emulator to exit before restarting Steam. Uses setsid to detach from
    # the launcher's process group — Steam's reaper kills all children when
    # the game exits, which would kill this background work otherwise.
    _deckdock_launcher_pid=$$
    setsid bash -c "
        python3 \"\$HOME/Emulation/tools/add-roms-to-steam.py\" >/dev/null 2>&1
        python3 \"\$HOME/Emulation/tools/fetch-boxart.py\" >/dev/null 2>&1
        # Wait for the emulator (which inherited the launcher PID) to exit
        while kill -0 $_deckdock_launcher_pid 2>/dev/null; do
            sleep 5
        done
        sleep 2
        steam -shutdown >/dev/null 2>&1
    " &
fi

# Clean up internal variables — the sourcing script continues as normal
unset _DECKDOCK_NAS_MOUNT _DECKDOCK_NAS_HOST _DECKDOCK_NAS_USER _DECKDOCK_NAS_EXPORT _DECKDOCK_SSH_KEY
unset _DECKDOCK_LOCKFILE _DECKDOCK_TMP_SUFFIX _DECKDOCK_MIN_FREE_MB
unset _cfg _deckdock_needs_download _deckdock_rom_arg _nas_target _rom_dir _rom_name
unset _nas_dir _ext _files_list _total_bytes _total_mb _free_mb _file_count _current
unset _failed _src_path _filename _dst_path _tmp_dst _label _main_filename _arg _src _sz _src_rel _line _binfile
unset _nas_remote _nas_remote_dir
unset _original_link _link_target _deckdock_fifo _deckdock_dl_pid _deckdock_poll_pid _deckdock_zenity_pid
unset _src_size _cur_size _rel_path _scp_src
unset _deckdock_launcher_pid
unset -f _deckdock_raise_zenity
