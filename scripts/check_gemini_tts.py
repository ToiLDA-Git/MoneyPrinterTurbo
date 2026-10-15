"""Redacted account verification. --synthesize explicitly runs short paid probes."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from app.services import gemini_speech


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--synthesize',action='store_true',help='Create short real speech for all three models; may use quota/credits')
    args=parser.parse_args()
    if not gemini_speech.api_key():
        print(json.dumps({'configured':False,'message':'Save a Gemini API key in existing application Settings first.'}))
        return 2
    report=gemini_speech.probe_models() if args.synthesize else gemini_speech.verify_models()
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if all(v.get('audio_ready',v['listed']) for v in report.values()) else 1


if __name__=='__main__':
    raise SystemExit(main())
