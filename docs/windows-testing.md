# Trying Homebound on Windows

Homebound is an offline wormhole mapper for EVE. It reads your Local chat logs and draws an
overlay over the client. The Windows version has not run on a real Windows PC yet, so thanks for
trying it.

You don't need EVE for most of it. Part A fakes the chat logs and the EVE window from PowerShell
and takes about 20 minutes. Part B is for a PC with EVE installed.

## 1. Install the tools (once)

Open **PowerShell** and run:

```powershell
winget install --id Git.Git -e
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then close PowerShell and open a new one, so it finds `git` and `uv`. You don't need to install
Python: `uv` downloads the right version.

## 2. Get it, build the universe data, run the tests

```powershell
git clone https://github.com/luto2/homebound.git
cd homebound
uv sync
uv run python -m homebound.universe.build_static
uv run pytest -q
```

`build_static` downloads the EVE static data export and the community wormhole data (about
100 MB) and writes `data\universe.sqlite`, which takes about a minute. The tests and the app need
it. The last line of the tests should say `… passed`. If anything fails, copy the whole output.

## 3. Start it

```powershell
uv run python -m homebound
```

The main window opens, and Settings opens on **Log folder**. The task bar should show
Homebound's icon, not Python's. Leave Settings open for now and go on with Part A (or Part B on a
PC with EVE).

Your data goes to `%APPDATA%\Homebound`. To remove everything afterwards, delete that folder, the
`homebound` folder you cloned and, after Part A, the fake `Documents\EVE` folder.

---

## Part A: without EVE

### A1. Make fake chat logs

Open a **second** PowerShell window (Homebound keeps the first one busy) and paste this whole
block. It defines two commands, `Login` and `Jump`, that write Local chat logs the way the EVE
client does: UTF-16 with a byte-order mark, in `Documents\EVE\logs\Chatlogs`.

```powershell
$logs = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "EVE\logs\Chatlogs"
New-Item -ItemType Directory -Force $logs | Out-Null
$pilots = @{}
function Stamp { (Get-Date).ToUniversalTime().ToString("yyyy.MM.dd HH:mm:ss") }
function Login($name, $id, $system) {
    $now = (Get-Date).ToUniversalTime()
    $file = Join-Path $logs ("Local_{0}_{1}.txt" -f $now.ToString("yyyyMMdd_HHmmss"), $id)
    $rule = "      " + ("-" * 63)
    $header = "`r`n`r`n$rule`r`n`r`n        Channel ID:      local`r`n        Channel Name:    Local`r`n" +
              "        Listener:        $name`r`n        Session started: $(Stamp)`r`n$rule`r`n`r`n"
    [IO.File]::WriteAllText($file, $header, [Text.Encoding]::Unicode)
    $script:pilots[$name] = $file
    Jump $name $system
}
function Jump($name, $system) {
    [IO.File]::AppendAllText($script:pilots[$name],
        "[ $(Stamp) ] EVE System > Channel changed to Local : $system`r`n", [Text.Encoding]::Unicode)
}
Login "Test Pilot" 90000001 "J161940"
```

### A2. Log folder

In Homebound's Settings, click **Auto-detect**. It should find `Documents\EVE\logs\Chatlogs`,
also when OneDrive has moved your Documents folder. The line under the field should report the one
log and one character it found. Set the home system to `J161940` and click **Save**.

Check: *Test Pilot* is in the Characters list on the left, in J161940.

### A3. Jumps

Type these in the second PowerShell window, one at a time, and watch Homebound after each:

| Type | Expected in Homebound |
|---|---|
| `Jump "Test Pilot" "J152820"` | A naming dialog ("was this the static?"). Answer it. J152820 appears on the map, linked to home, and the pilot moves there. |
| `Jump "Test Pilot" "Jita"` | Another new system, Jita, linked to J152820. |
| `Jump "Test Pilot" "Perimeter"` | Gate jump: the pilot moves, nothing is added. |
| `Jump "Test Pilot" "Amarr"` | No gate between them: a toast asks "Unknown jump … Add as WH connection?". |
| `Login "Second Pilot" 90000002 "J161940"` | A second character appears in the list, at home. |

### A4. Signatures

Select home on the map, copy these lines (Ctrl+C), click into the Homebound window and press
**Ctrl+V**:

```text
ABC-123	Cosmic Signature	Wormhole	Unstable Wormhole	100,0%	4,32 AU
DEF-456	Cosmic Signature			12,5%	10 AU
GHI-789	Cosmic Signature	Gas Site	Barren Perimeter Reservoir	100,0%	2 AU
```

Check: three sigs in the table on the right, grouped under Wormholes, Gas and Unscanned. If the
paste does nothing, the browser or editor may have turned the tabs into spaces; paste the lines
into Notepad first and copy them from there.

### A5. Overlay and focus tracking

Homebound decides which character to show from the title of the window in front, which for EVE
is `EVE - <character name>`. A PowerShell window can pretend to be one:

1. In Homebound's toolbar, set the overlay to **Follow**.
2. In the second PowerShell window type `$host.UI.RawUI.WindowTitle = "EVE - Test Pilot"` and
   keep that window in front.

Check:
- The status bar (bottom of the main window) says *Windows focus: active*.
- The overlay appears and shows Test Pilot's chain.
- Click any other window: the overlay hides. Click the PowerShell window again: it comes back.
- Tray icon → *Unlock overlay*, drag the overlay somewhere else, then lock it again. Do clicks go
  through it to the window below while it's locked?

If you use Windows Terminal and the title doesn't change, try the classic console: Win+R →
`conhost powershell`, paste the A1 block again but without the last line (`Login …`), and set the
title there.

### A6. Everything else

1. **Tray**: close the main window. Homebound stays in the tray, and clicking the icon brings it
   back.
2. **Notifications**: close the main window again, then type `Jump "Test Pilot" "J160650"`. Do you
   get a Windows notification?
3. **Settings → Backups**: click *Back up now*, then *Open folder*. Does Explorer open on a folder
   with a zip in it?
4. **Maps**: ▦ menu → *New map …*, then switch back to *Main*.
5. **Looks**: does text fit in the boxes on the map, the sig table and the dialogs, or is it cut
   off? Are the fonts the same as in the screenshots in the README?

---

## Part B: with EVE

1. In EVE: *Settings → Chat → Log chat to file* on, and run the client **windowed** or
   **borderless (fixed window)**. Exclusive fullscreen hides the overlay; that's a Windows limit.
2. In Homebound's Settings, click **Auto-detect**, set any home system (for example `J161940`)
   and click **Save**.
3. Log in a character. Does it show up in the list on the left, with its system? Jump through a
   gate or wormhole. Does it move on the map? This checks that Homebound can read the logs while
   EVE has them open.
4. Set the overlay to **Follow** and click into the EVE client. Does the overlay appear over EVE
   and stay on top while you play? Does it switch when you change between two clients?
5. Do A4 with a real probe-scanner paste (Ctrl+A, Ctrl+C in the scanner, Ctrl+V in Homebound),
   and A6.

---

## Optional: the packaged build

This tries the build that a release ships, starting with an empty data folder:

```powershell
uv run --group packaging pyinstaller packaging/homebound.spec --noconfirm --workpath build/pyinstaller --distpath dist
$env:HOMEBOUND_HOME = "$env:TEMP\homebound-test"
dist\Homebound\Homebound.exe
```

It should ask to download the map data, since the test folder is empty, and then start like
before. Close it from the tray and run `Remove-Item Env:HOMEBOUND_HOME` afterwards.

## Send back

- Which steps worked and which didn't, by number (A3, A5 …).
- Screenshots of anything that looks wrong.
- The text the first PowerShell window printed, especially any `Traceback` (copy all of it).
- Your Windows version (Win+R → `winver`), and whether your Documents folder is in OneDrive.
