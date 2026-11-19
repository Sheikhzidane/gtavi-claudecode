"""Build a synthetic, fully owned 1920x1080 sample 'gameplay' video with speech, for end-to-end tests."""
import asyncio, subprocess, sys
from pathlib import Path
sys.path.insert(0, "scripts")
from common import tool
import edge_tts
D = Path("test/sample"); D.mkdir(parents=True, exist_ok=True)
LINES = [
 "Okay, we are finally in Vice City. Let's see what this car can do.",
 "Wait, wait, the cops are behind me. Three stars already, no way!",
 "Oh my god, the helicopter is chasing us. Go go go, take the bridge!",
 "Bro, I just jumped the whole river. That is insane. Let's go!",
 "Alright, quiet moment. Look at this, there's a hidden room behind the bar.",
 "I have never seen this before. This is a secret Easter egg, for real.",
 "Nah, what the, the guy just stole my boat. LOL, that's so funny.",
 "Okay, the mission starts now. The heist at the bank, this is the big one.",
 "Wait for it, wait for it, oh, we failed. Wasted. Why did I do that?",
 "That was crazy. GTA six is unreal. Okay, one more chase before we stop.",
]
async def tts():
    for i, t in enumerate(LINES):
        await edge_tts.Communicate(t, "en-US-AriaNeural").save(str(D / f"l{i}.mp3"))
if not (D / 'l9.mp3').exists():
    asyncio.run(tts())
ff = tool("ffmpeg")
# each line: speech followed by ~5 s of gap with a loud 'engine' burst on hype lines
parts = []
for i in range(len(LINES)):
    seg = D / f"s{i}.wav"
    burst = "0.9" if i in (1, 2, 3, 8) else "0.15"
    subprocess.run([ff, "-y", "-v", "error", "-i", str(D / f"l{i}.mp3"), "-f", "lavfi", "-t", "5",
        "-i", f"anoisesrc=color=brown:amplitude={burst}:r=48000", "-filter_complex",
        "[0:a]aresample=48000,aformat=channel_layouts=stereo[a];[1:a]aformat=channel_layouts=stereo,afade=t=out:st=3.5:d=1.5[b];[a][b]concat=n=2:v=0:a=1[o]",
        "-map", "[o]", str(seg)], check=True)
    parts.append(seg)
(D / "list.txt").write_text("".join(f"file '{p.name}'\n" for p in parts))
subprocess.run([ff, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(D / "list.txt"), "-c:a", "pcm_s16le", str(D / "audio.wav")], check=True)
dur = float(subprocess.run([tool("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(D / "audio.wav")],
                           capture_output=True, text=True).stdout)
font = r"C\:/Windows/Fonts/arialbd.ttf"
vf = ("hue=h=t*20,scale=1920:1080:flags=neighbor," f"drawtext=fontfile='{font}':text='OWNED TEST FOOTAGE - SYNTHETIC SAMPLE':fontsize=48:fontcolor=white:box=1:boxcolor=black@0.6:x=(w-tw)/2:y=60,"
      rf"drawtext=fontfile='{font}':text='%{{pts\:hms}}':fontsize=40:fontcolor=yellow:x=60:y=h-100")
subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", "life=s=480x270:mold=10:r=30:ratio=0.08:death_color=#101030:life_color=#ff4fa0",
                "-i", str(D / "audio.wav"), "-t", f"{dur:.2f}", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-shortest", str(D / "own_sample_gameplay.mp4")], check=True)
print(D / "own_sample_gameplay.mp4", f"{dur:.1f}s")
