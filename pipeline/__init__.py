"""Internal video pipeline — replaces the external n8n workflow.

Flow (runner.py drives it):
    /create → runner.submit()
      script    : llm.write_script()      LLM writes narration + scene prompts
      assemble  : assembly.assemble()     style block injected mechanically
      rendering : modal_client            async submit → poll → fetch mp4
      uploading : storage.upload_video()  R2, unguessable key
      delivered : DB complete + DM

Enabled with PIPELINE=internal; PIPELINE=n8n keeps the legacy path untouched.
"""
