Satellite Radio

A desktop app that lets you tune a slider across the satellite bands and listen to real satellite signals, no antenna or SDR required.

When the slider lands on the frequency of an active satellite transmitter, the app shows SIGNAL FOUND, tells you which satellite it is, and plays the newest real recording of that satellite made by an amateur ground station on the SatNOGS network.

Important: this is not live reception. The audio comes from recent recordings uploaded by volunteers around the world. Receiving satellites live requires hardware (see Going live).

Features
One frequency slider with three bands: 137 MHz (weather), 145 MHz (2 m amateur), 437 MHz (70 cm amateur)
5 kHz fine-tuning buttons (◀ ▶)
Real transmitter frequencies from the SatNOGS DB (active transmitters only)
"Nearby" hints showing satellites close to the current frequency
Plays the newest "good" recording and lists older ones to choose from
Volume control, stop button, and a link to the recording's SatNOGS page (with the waterfall/spectrogram)
Requirements
Python 3.9+
VLC media player installed (same architecture as Python, e.g. 64-bit)
Python packages:
python -m pip install requests customtkinter python-vlc
Run
python satellite_radio_slider.py
Usage
Wait for "active satellite transmitters loaded".
Pick a band, then drag the slider (use ◀ ▶ for fine steps).
Stop moving for a moment. If a transmitter is within 15 kHz, the app locks on and starts playing.
Click another recording in the list to hear a different pass, or press Open page to see it on SatNOGS.
Frequencies to try
Frequency	Signal	What you will hear
137.1000 / 137.6200 / 137.9125 MHz	NOAA weather satellites (APT)	Steady "tick-tick" twice per second inside the noise. This is the image signal.
145.8000 MHz	International Space Station (FM voice)	Mostly noise or silence, occasionally crew radio contacts
436.795 MHz	SO-50 amateur FM satellite	Short FM passes

Some satellites get decommissioned over time, so a frequency may return "no signal" or no recordings. The "Nearby:" line shows what is currently listed.

Notes and limitations
Static is normal. Satellite recordings are noisy by nature, and most are not speech.
Anonymous SatNOGS API use is rate-limited (about 60 observation requests per hour). Results are cached per transmitter to reduce requests.
Recordings are downloaded to a temporary .ogg file and played with VLC, then deleted.
A satellite shown in the list is not necessarily still operating. Check its page on db.satnogs.org.
Ham radio operators identify themselves with call signs. Equipment serial numbers are never transmitted.
Troubleshooting
Problem	Fix
Database error / timeout / SSL error	Turn off your VPN or proxy and try again. Some VPN exit nodes are blocked.
HTTP 429	Rate limit reached. Wait a while.
No sound, status says "Playing"	Check the Windows volume mixer for VLC; try a different recording.
Status says "VLC could not play the file"	Pick another recording; make sure VLC and Python are both 64-bit.
"No playable recordings"	That transmitter has no recent good observations. Try another frequency.
Going live

To receive satellites in real time you need an SDR receiver such as an RTL-SDR dongle, a suitable antenna (a V-dipole is enough for 137 MHz) and a small program that tunes the dongle, applies Doppler correction, and demodulates FM. This repository's slider app does not include that.

Data sources and credits
Transmitter frequencies and satellite names: SatNOGS DB, community data licensed CC-BY-SA 4.0
Recordings: SatNOGS Network and the volunteer station owners who record them
UI: CustomTkinter, audio playback: python-vlc

This project is not affiliated with SatNOGS or the Libre Space Foundation.

License

Add a license of your choice (for example MIT) before publishing.
