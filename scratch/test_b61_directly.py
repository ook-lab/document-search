# -*- coding: utf-8 -*-
import sys
from pathlib import Path

# Add services/pipeline-lab to sys.path so we can import dms
sys.path.append(str(Path(__file__).resolve().parent.parent / "services" / "pipeline-lab"))

from dms.pipeline.stage_b.b61_pdf_word_ltsc import B61PDFWordLTSCProcessor

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    pdf_path = Path(r"C:\Users\ookub\document-management-system\services\pipeline-lab\uploads\pipeline_lab\11a97d2f510a\input.pdf")
    if not pdf_path.exists():
        print(f"Error: {pdf_path} does not exist.")
        sys.exit(1)
        
    print(f"Running B61PDFWordLTSCProcessor on: {pdf_path}")
    processor = B61PDFWordLTSCProcessor()
    
    # Run the processor
    try:
        res = processor.process(pdf_path)
        print("Success:", res.get("is_structured", False))
        print("Keys in result:", list(res.keys()))
        if "error" in res:
            print("Error details:", res["error"])
    except Exception as e:
        print("Exception occurred:")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
