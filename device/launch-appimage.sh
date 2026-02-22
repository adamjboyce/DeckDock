#!/bin/bash
# Wrapper for launching emulators from Steam shortcuts.
# Clears Steam's LD_PRELOAD (overlay library causes segfaults in emulators)
# and keeps bash as parent process so Steam's reaper can track it.
unset LD_PRELOAD
unset LD_LIBRARY_PATH
"$@"
