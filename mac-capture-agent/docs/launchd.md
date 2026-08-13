# launchd on-demand setup

The LaunchAgent label is `com.homelab.mac-capture-agent`. It is registered with
`RunAtLoad=false` and `KeepAlive=false`, so bootstrap does not start it and launchd
does not restart it after termination.

Production files use this user-local directory:

```text
~/Library/Application Support/Homelab/mac-capture-agent/
```

The tracked plist contains no secret. A small wrapper and a mode-`600` environment
file live outside Git in that directory. The wrapper loads the token and replaces
itself with the production binary.

## Build and install manually

Run these commands as the `mar3k` user. Replace the placeholder token only in the
local `agent.env` file; do not paste a real token into the repository or plist.

```bash
cd /Users/mar3k/Projects/audio-live/mac-capture-agent
swift build -c release

install -d -m 700 "/Users/mar3k/Library/Application Support/Homelab/mac-capture-agent"
install -m 755 .build/release/mac-capture-agent \
  "/Users/mar3k/Library/Application Support/Homelab/mac-capture-agent/mac-capture-agent"

umask 077
cat > "/Users/mar3k/Library/Application Support/Homelab/mac-capture-agent/agent.env" <<'EOF'
MAC_CAPTURE_AGENT_TOKEN='replace-with-the-shared-token'
EOF

cat > "/Users/mar3k/Library/Application Support/Homelab/mac-capture-agent/run-mac-capture-agent" <<'EOF'
#!/bin/zsh
set -eu
set -a
source "/Users/mar3k/Library/Application Support/Homelab/mac-capture-agent/agent.env"
set +a
: "${MAC_CAPTURE_AGENT_TOKEN:?MAC_CAPTURE_AGENT_TOKEN must be set and non-empty}"
exec "/Users/mar3k/Library/Application Support/Homelab/mac-capture-agent/mac-capture-agent"
EOF
chmod 700 "/Users/mar3k/Library/Application Support/Homelab/mac-capture-agent/run-mac-capture-agent"

install -d -m 700 "/Users/mar3k/Library/Logs/Homelab"
install -d -m 700 "/Users/mar3k/Library/LaunchAgents"
install -m 600 deploy/com.homelab.mac-capture-agent.plist \
  "/Users/mar3k/Library/LaunchAgents/com.homelab.mac-capture-agent.plist"

launchctl bootstrap "gui/$(id -u)" \
  "/Users/mar3k/Library/LaunchAgents/com.homelab.mac-capture-agent.plist"
```

`bootstrap` only registers this on-demand job. It does not start the agent.

## Control and inspect

```bash
# Status (a non-running, registered on-demand job is expected before kickstart)
launchctl print "gui/$(id -u)/com.homelab.mac-capture-agent"

# Start the process
launchctl kickstart "gui/$(id -u)/com.homelab.mac-capture-agent"

# Health check after start
curl http://localhost:8011/health

# Gracefully terminate the process with SIGTERM
launchctl kill SIGTERM "gui/$(id -u)/com.homelab.mac-capture-agent"

# Confirm that the port is no longer listening
lsof -nP -iTCP:8011 -sTCP:LISTEN
```

Pausing a recording uses the existing HTTP endpoint and leaves the process alive.
Stopping a recording also uses the existing HTTP endpoint; Audio Lab can then issue
the separate `launchctl kill SIGTERM` command to terminate the process.

## Unregister and uninstall manually

Stop and unregister the job before removing its installed plist. The production
binary, wrapper, secret file, and logs are intentionally separate; remove them only
when a full local uninstall is wanted.

```bash
launchctl kill SIGTERM "gui/$(id -u)/com.homelab.mac-capture-agent" 2>/dev/null || true
launchctl bootout "gui/$(id -u)" \
  "/Users/mar3k/Library/LaunchAgents/com.homelab.mac-capture-agent.plist"
rm "/Users/mar3k/Library/LaunchAgents/com.homelab.mac-capture-agent.plist"
```

For a full uninstall, optionally remove these explicit user-local files after
`bootout`:

```bash
rm "/Users/mar3k/Library/Application Support/Homelab/mac-capture-agent/mac-capture-agent"
rm "/Users/mar3k/Library/Application Support/Homelab/mac-capture-agent/run-mac-capture-agent"
rm "/Users/mar3k/Library/Application Support/Homelab/mac-capture-agent/agent.env"
```
