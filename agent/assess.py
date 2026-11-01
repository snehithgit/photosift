"""One leased original to one D3 Nano assessment, through the model gateway on this laptop."""

import base64

from agent.embed import AGENT_INPUT, claimed_image

QUESTION_VERSION = "photosift-d3-questions-v1"
STATE = "Sort a private photo library. Judge only what is visible in the image, not its file name. Do not identify anyone."
QUESTIONS = {
    "sensitive": {
        "type": "noul",
        "instructions": "Does this image show private information someone would not want shared?",
        "criteria": {
            "false": "Nothing private: an ordinary scene, object, screen or document with no personal records",
            "true": "An identity document, passport, payment or bank card, financial or medical record, password or login details, or nudity",
        },
    },
    "important": {
        "type": "noul",
        "instructions": "Could this image preserve a personal memory or useful information worth keeping, regardless of its content type?",
    },
    "people": {"type": "noul", "instructions": "Are people visible in the image?"},
    "text": {"type": "noul", "instructions": "Is readable text visible in the image?"},
    "content": {
        "type": "choice",
        "instructions": "Which content type best describes this image?",
        "criteria": {
            "personal_photo": "A camera photograph of a personal memory: people, pets, places, nature or activities",
            "screenshot": "A captured phone or computer screen showing an app, website, game or conversation",
            "meme_graphic": "A meme, joke, greeting, inspirational quote, illustration or forwarded graphic",
            "document": "A receipt, ticket, certificate, form, identity document or other informational record",
            "other": "An image which cannot be reliably assigned to the other categories",
        },
    },
}


def contract(d3):
    """Everything that shapes an answer. A change here is a different assessment, never a mix."""
    return {
        "protocol": d3["protocol"],
        "model": d3["model"],
        "modelDigest": d3["digest"],
        "runtime": d3["runtime"],
        "questionVersion": QUESTION_VERSION,
        "state": STATE,
        "questions": QUESTIONS,
        "input": dict(AGENT_INPUT),
    }


def assess_payload(client, claim, root_mappings, gateway, target):
    if claim["fingerprint"] != target["fingerprint"]:
        raise ValueError("Claim fingerprint does not match this executor")
    image = claimed_image(client, claim, root_mappings)
    return gateway.assess(base64.b64encode(image).decode("ascii"), STATE, QUESTIONS)
