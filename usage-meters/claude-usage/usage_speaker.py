"""Speak the over-cap usage boxes on repeat. Run with the Piper venv's Python.

Reads the phrase from the text file given as argv[1] every cycle, so the caller can
update it while this runs. Exits when the file is gone or empty.
"""
import hashlib, os, subprocess, sys, time, wave
from pathlib import Path

ONCE = sys.argv[1] == '--once'  # '--once PHRASE': speak a single reminder, then exit
TEXT = None if ONCE else Path(sys.argv[1])
HOME = Path.home()
VOICES = HOME / '.local/share/claude-usage/tts/voices'
VOICE = os.environ.get('CLAUDE_USAGE_VOICE', 'en_US-amy-medium')
CACHE = HOME / '.cache/claude-usage-speech'
CACHE.mkdir(parents=True, exist_ok=True)

voice = None
def render(text):
    global voice
    out = CACHE / (hashlib.sha256((VOICE + text).encode()).hexdigest()[:16] + '.wav')
    if not out.exists():
        try:
            if voice is None:
                from piper import PiperVoice
                voice = PiperVoice.load(str(VOICES / (VOICE + '.onnx')))
            tmp = out.with_suffix('.tmp')
            with wave.open(str(tmp), 'wb') as w:
                voice.synthesize_wav(text, w)
            tmp.rename(out)
        except Exception:
            return None
    return out

def speak(text):
    wav = render(text)
    if wav:
        subprocess.run(['/usr/bin/pw-play', str(wav)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:  # Piper unavailable: fall back to eSpeak so the warning is never silent
        subprocess.run(['/usr/bin/espeak-ng', '-s', '150', text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

if ONCE:
    speak(' '.join(sys.argv[2:]).strip())
    sys.exit(0)

while True:
    try:
        text = TEXT.read_text().strip()
    except OSError:
        break
    if not text:
        break
    speak(text)
    time.sleep(2)
