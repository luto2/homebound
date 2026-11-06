# Mockup summaries

One file per mockup in `design-system/previews/`. Each says what the mockup shows, where its spec is and
which code implements it, so the knowledge graph can link a design to its code without parsing the HTML.

The colours, sizes and fonts all mockups use come from `design-system/tokens.json`. The app reads its copy,
`homebound/theme/tokens.json`, through `homebound/theme/tokens.py`.

- [BuildDataDialog](BuildDataDialog.md): First-run dialog that downloads the map data and builds it, before the main window exists.
- [Button](Button.md): Push and tool buttons in primary, default, danger and ghost variants.
- [CharacterMarker](CharacterMarker.md): 14px shape that shows where a character is. Slot N always pairs one colour with one shape.
- [ClassBadge](ClassBadge.md): 16px label that names a space class. Wormhole classes are filled, known space is outlined with its security status.
- [ConfirmDialog](ConfirmDialog.md): The one action that asks first: deleting a map. Every other destructive action shows an Undo toast.
- [ConnectionEdge](ConnectionEdge.md): The curve between two systems, with a label plate. States differ by width and dash first, colour second.
- [ContextMenu](ContextMenu.md): The right-click menus, which share one set of rules for captions, destructive items and disabled items.
- [Cover](Cover.md): Title card of the design system, with the wordmark and tagline.
- [Dialog](Dialog.md): The shared modal frame: title, subtitle, body, and a footer with the buttons.
- [EffectIcon](EffectIcon.md): One glyph per wormhole effect, each with its own silhouette.
- [MapMenu](MapMenu.md): Switches between maps and manages them. Each map is its own SQLite file.
- [MapWindow](MapWindow.md): The main window: toolbar, character and route docks, map canvas, system dock and status bar.
- [NamingDialog](NamingDialog.md): Asks "Was this the static?" when a character enters an unmapped system, with the likely answer selected.
- [OverlayRow](OverlayRow.md): One line of the overlay: name, class, J-name, effect, kills and owner, readable over any game background.
- [OverlayStates](OverlayStates.md): The overlay as a whole, over different game backdrops.
- [OwnerTag](OwnerTag.md): Coloured chip with the ticker of who lives in a system.
- [PromptDialog](PromptDialog.md): One captioned text field with inline validation, used for four dialogs.
- [RestoreBackupDialog](RestoreBackupDialog.md): Restores maps from one backup, as new maps or by replacing them.
- [RoutesDock](RoutesDock.md): Left dock with the routes from the selected system to each destination.
- [Settings](Settings.md): The settings window: a nav list and one page per topic, each showing its live result.
- [SigEditor](SigEditor.md): Dialog for what the scanner can't show: wormhole type, lifetime, mass and, for a K162, destination and size.
- [SignatureTable](SignatureTable.md): Grouped list of the signatures and anomalies of the selected system.
- [SiteIcon](SiteIcon.md): One icon per signature group, using EVE's own bracket icons, tinted by brightness.
- [StartupError](StartupError.md): Error dialog for missing map data. Replaced in the app by BuildDataDialog.
- [StaticBadge](StaticBadge.md): Small "→ C5" chip that names where a static leads.
- [StatusBar](StatusBar.md): Bottom strip with the state of each background source.
- [SystemNode](SystemNode.md): One system on the map, 168 by 62 at 100% zoom, with three levels of detail.
- [TextField](TextField.md): Text inputs, combos, path pickers, checkboxes, radios and segmented choices.
- [Toast](Toast.md): Non-modal notice in the bottom-right of the map. It never takes focus from the game.
- [TrayMenu](TrayMenu.md): Context menu of the tray icon: overlay on or off, unlock, overlay mode and window actions.
- [ZkillMeter](ZkillMeter.md): Three-bar activity meter, plus a diamond for a kill in the last 10 minutes.
