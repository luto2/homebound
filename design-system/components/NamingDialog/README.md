# NamingDialog

The question the app asks when a character enters an unmapped system: "Was this the static?", with the likely answer already selected.

- Opens over the map with a 65% `bg-0` scrim, centred on the map area, not the screen, so the user still sees the two nodes being joined.
- Both answers show the **resulting name** in mono (`A2a` vs `A2-1`) from the active naming scheme, so the user picks an outcome, not an abstract yes/no.
- The guess is preselected, marked "BEST GUESS", and the help line says *why* (static class match, sig type, other unlinked holes). Enter accepts; Y / N pick directly; Esc cancels and leaves the system unnamed.
- The name field below is editable and updates with the radio; typing in it overrides the scheme.
- "Don't ask when the guess is certain" skips the dialog next time (a toast confirms the auto-name and offers Undo).
