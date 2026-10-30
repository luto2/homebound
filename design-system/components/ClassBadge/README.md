# ClassBadge

A 16px label that names a system's space class; the text is the primary signal and the colour supports it.

- **W-space** (C1–C6, TH Thera, C13, DR Drifter, PV Pochven): filled with its `class-*` token, text `ink-inverse`, `node-meta` style (10px/700).
- **K-space** (HS, LS, NS): *outlined* in its `class-*` colour with the security status in mono after the label. Filled vs outlined separates k-space from w-space even in greyscale.
- Shape cues for the odd ones: C13 has a crack notch, Pochven a cut top-right corner.
- Never invent abbreviations beyond these 13 labels.

Qt (QPainter): `drawRoundedRect(r, 2, 2)` with the class brush, or a 1.5px pen for k-space; C13/Pochven via a `QPainterPath`. Cache one `QPixmap` per class/security pair at the device pixel ratio.
