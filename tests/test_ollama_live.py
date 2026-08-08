"""Opt-in local Ollama smoke test; never runs in the offline suite by default."""

import os
import unittest

from PIL import Image

from fd_training_ocr.recognition import OllamaVisionProvider, RecognitionError, make_request
from fd_training_ocr.template import Region


@unittest.skipUnless(os.environ.get("FD_OCR_LIVE_OLLAMA") == "1",
                     "set FD_OCR_LIVE_OLLAMA=1 after installing Ollama, starting it, and pulling the configured vision model")
class OllamaLiveTests(unittest.TestCase):
    def test_local_service_and_model(self):
        provider = OllamaVisionProvider(model=os.environ.get("FD_OCR_OLLAMA_MODEL", "qwen2.5vl:7b"))
        request = make_request(Image.new("L", (100, 50), 255),
                               Region("date", "text", (.1, .1, .8, .8), {}))
        try:
            result = provider.recognize(request)
        except RecognitionError as exc:
            self.skipTest(f"{exc} Set FD_OCR_OLLAMA_MODEL if using another installed vision model.")
        self.assertEqual(result.provider, "ollama")


if __name__ == "__main__":
    unittest.main()
