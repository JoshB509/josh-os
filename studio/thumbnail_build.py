"""Build one YouTube thumbnail with Josh's approved scripts. Run only by Josh OS:

    python thumbnail_build.py <job.json>

job.json is written by video_thumbnails.py after it has checked every value. It holds
{"format": "middle" | "close", "spec": {...}, "out": "<png path inside the video's thumbnails folder>"}.

This file is the whole boundary for what a thumbnail job can run. It imports one approved
build script from the thumbnail tools folder, points that script's file paths at the assets on
this PC (the cloud workspace paths in the scripts don't exist here), and calls its build().
Nothing else is run, nothing is read from the job but plain values, and the output path is
checked again here before anything is written.
"""
import json
import sys
from pathlib import Path

TOOLS = Path(r'E:\CareerOS\12_YouTube\_thumbnail-system\tools')
TEMPLATE = Path(r'C:\Users\Anigma PC\Documents\Codex\2026-10-24\task\Josh Photoshop Template')
FONT = Path(r'C:\Users\Anigma PC\Downloads\Josh Thumbnail Font Kit v2\Josh Thumbnail Font Kit v2\Font\JoshThumbnailV2-BoldItalic.ttf')
YOUTUBE = Path(r'E:\CareerOS\12_YouTube')
# format -> the approved script's module name in TOOLS
FORMATS = {'middle': 'oneline', 'close': 'close_unit'}
BACKGROUNDS = {
    'seated': TEMPLATE / 'final-scenes' / 'Scene Assets' / 'Backgrounds' / '01-seated-office.png',
    'standing': TEMPLATE / 'final-scenes' / 'Scene Assets' / 'Backgrounds' / '02-standing-office.png',
    'close': TEMPLATE / 'final-scenes' / 'Scene Assets' / 'Backgrounds' / '03-closeup-office.png',
}


def main(job_path):
    job = json.loads(Path(job_path).read_text(encoding='utf-8'))
    module = FORMATS[job['format']]
    out = Path(job['out']).resolve()
    # The output must be a new PNG in a video's own thumbnails folder.
    if out.suffix != '.png' or out.parent.name != 'thumbnails' or out.parent.parent.parent != YOUTUBE.resolve() or out.exists():
        raise SystemExit('Refused: the output must be a new .png in a video\'s thumbnails folder.')
    pose = Path(job['spec']['pose']['path']).resolve()
    if not pose.is_relative_to((TEMPLATE / 'Pose Catalogue').resolve()) or pose.suffix.lower() != '.png' or not pose.is_file():
        raise SystemExit('Refused: the pose must be a cutout from the Pose Catalogue.')
    if job['spec']['scene'] not in BACKGROUNDS:
        raise SystemExit('Refused: unknown scene.')
    sys.path.insert(0, str(TOOLS))
    import cv2
    import thumb
    thumb.U = str(TEMPLATE)
    thumb.BG = {k: str(v) for k, v in BACKGROUNDS.items()}
    thumb.FONT = str(FONT)
    thumb._casc = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    import oneline
    oneline.FONT = str(FONT)
    builder = __import__(module)
    builder.FONT = str(FONT)
    result = builder.build(job['spec'], str(out))
    print(json.dumps(dict(out=str(out), result=result), default=str))


if __name__ == '__main__':
    main(sys.argv[1])
