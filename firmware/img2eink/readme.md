# Script to convert images to show them on the Eink screen
Prerequisites: Krita, Python  
Micropython installed > https://micropython.org/download/RPI_PICO2/  


1. Sketch your design in krita.
2. Export to png. (With all of the seting aready set up)
3. For a custom NFC profile, copy its example and edit the values:
	- URL: copy `nfc.url.example.json` to `nfc.url.json` and edit.
	- vCard: copy `nfc.vcard.example.json` to `nfc.vcard.json` and edit. Optional fields include name parts, nickname, birthday, organization, title, role, work phone/email, address, URL, and note. Use `\n` in `note` for a line break; address `type` can be `HOME` or `WORK`.
4. Run `python firmware/img2eink/img2code.py Badge.png`
	- URL: `python firmware/img2eink/img2code.py Badge.png --publish url`
	- vCard: `python firmware/img2eink/img2code.py Badge.png --publish vcard`
5. Copy the resulting *code.py* to the root of the Micropython drive.
6. Enjoy!

With a NFC payload, hold Button A (GP5) at boot to write and verify the NFC payload, then restore RF write protection. Hold Button B (GP4) at boot to leave RF writes enabled for the whole boot. The e-paper refresh runs after NFC handling as a completion signal.

GP5 Will load the NFC payload once.
GP4 Will unlock the NFC IC at boot.