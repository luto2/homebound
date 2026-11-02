# Trying Homebound on Windows

Homebound is an offline wormhole mapper for EVE. It reads your Local chat logs and draws an
overlay over the client. The Windows version is brand new and has never run on real Windows, so
thanks for trying it. It takes about 10 minutes.

## 1. Install the tools (once)

Open **PowerShell** and run:

```powershell
winget install --id Git.Git -e
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then close PowerShell and open a new one, so it finds `git` and `uv`. You don't need to install
Python: `uv` downloads the right version.

## 2. Get it and set it up

```powershell
git clone https://github.com/luto2/homebound.git
cd homebound
uv sync
```

On first start Homebound offers to download EVE's static data (systems, wormhole statics) and
build its map data; that takes a few minutes, once. After that it doesn't go online, except for
optional zKillboard lookups.

## 3. In EVE

- **Settings → Chat → Log chat to file**: on.
- Run the client in **windowed** or **borderless (fixed window)** mode. Exclusive fullscreen
  hides the overlay; that's a Windows limit.

## 4. Start it

```powershell
uv run python -m homebound
```

First click **Download** in the map data dialog and wait for it to finish. Then Settings opens on **Log folder**. Click **Auto-detect**. It should find
`Documents\EVE\logs\Chatlogs`, even if OneDrive moved your Documents folder. Then set any home
system, for example `J161940`, and click **Save**.

Your data goes to `%APPDATA%\Homebound`. To remove everything afterwards, delete that folder and
the `homebound` folder you cloned.

## 5. What to check

Please note what happens for each item:

1. **Log folder**: did Auto-detect find it? If not, where are your logs?
2. **Characters**: log in a character. Does it show up in the list on the left, with its system?
   Jump through a gate or wormhole. Does it move on the map? (This checks that Homebound can read
   the logs while EVE has them open.)
3. **Status bar** (bottom of the window): what does it say? It should include
   *Windows focus: active*.
4. **Overlay**: in the toolbar, set the overlay mode to **Follow**, then click into the EVE
   client. Does the overlay appear over EVE? Does it stay on top while you play? Does it switch
   when you change between two clients?
5. **Moving the overlay**: right-click the tray icon → *Unlock overlay*, drag it, then lock it
   again. Do clicks go through to EVE while it's locked?
6. **Notifications**: close the main window (Homebound stays in the tray) and jump into a new
   system. Do you get a Windows notification?
7. **Looks**: does text fit in the boxes on the map, or is it cut off?

## 6. Send back

- Your notes on the list above.
- Screenshots of anything that looks wrong.
- The text the PowerShell window printed, especially any `Traceback` (copy all of it).
- Your Windows version (Win+R → `winver`).
