# Script to convert images to show them on the Eink screen
Prerequisites: Krita, Python  
Micropython installed > https://micropython.org/download/RPI_PICO2/  

1. Sketch your design in krita.
2. Export to png. (With all of the seting aready set up)
3. run `python firmware/img2eink/img2code.py`
4. copy the resulting *code.py* to the root of the Micropython drive
5. enjoy!