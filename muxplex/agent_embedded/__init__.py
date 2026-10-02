"""Public amplifier_agent SDK integration; no kernel or CLI dependencies.

Browser protocol v1 keeps callback execution in the browser's existing executor
and confirmation UI while one public SDK turn stays live. Ordinary legacy
text/image requests use ephemeral sessions; legacy tool transcripts are refused.
"""
