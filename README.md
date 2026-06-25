# Codex Usage Widget

A compact local desktop widget for Codex CLI usage windows.

It reads Codex's local SQLite state under `~/.codex`:

- `logs_2.sqlite` for the most recent `codex.rate_limits` event
- `state_5.sqlite` for the most recently active CLI thread and model

This does not call OpenAI or burn tokens. The displayed limits are only as fresh as the latest rate-limit event Codex has already logged locally.

## Behavior

- The small window stays above other windows, turn it off in settings.
- Closing or minimizing the window sends it to the tray instead of quitting.
- The tray icon tooltip shows the same time information.
- Right click the tray icon for Show Window, Refresh, Settings, and Exit.
- The arrow in the widget expands local context details.
- Refresh rereads local SQLite files only.
- Settings controls border, widget layout, theme, and autostart.
- Settings also controls whether closing minimizes to tray.
- Theme can be `System`, `Dark`, or `Light`.
- The widget can be displayed horizontally or vertically stacked.
- The tray icon always follows the system theme.
- The main widget and taskbar icon use the colored icon automatically.
- Autostart is implemented through a desktop entry under `~/.config/autostart`.

## Requirements

- Python 3
- PySide6
- Codex CLI with local state under `~/.codex`
- A Qt/KDE Plasma desktop environment is recommended


## Run

```bash
git clone https://github.com/blakkguard/codex-usage-widget-qt.git
cd codex-usage-widget-qt

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

./run.sh
```


## Compatibility

This app has only been tested in a Qt/KDE Plasma environment.

Known tested environment:

* KDE Plasma 6.7
* Qt-based desktop session
* Fedora 44 KDE

Ubuntu 26.04 GNOME was briefly tested in a virtual machine, but the widget did not look or behave correctly there.

For now, treat this as a Qt/KDE-tested app, not a polished cross-desktop Linux tray widget.

