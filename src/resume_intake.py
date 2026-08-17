import argparse
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config import (
    DEFAULT_RESUME_FILE,
    EXTRACTED_USER_PROFILE_FILE,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_PROVIDER,
    RESUME_TEXT_OUTPUT_FILE,
    USER_PROFILE_EXTRACTION_FORCE_REFRESH,
    USER_PROFILE_EXTRACTION_MODEL,
    USER_PROFILE_EXTRACTION_PROMPT_VERSION,
    USER_PROFILE_EXTRACTION_SCHEMA_VERSION,
    USER_PROFILE_FILE,
    ensure_project_directories,
)
from src.models import UserProfile, utc_now_iso
from src.storage import load_json, save_json, save_text


SUPPORTED_RESUME_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}


class ResumeIntakeError(Exception):
    """
    Raised when resume intake or UserProfile extraction fails.
    """


@dataclass
class UserProfileExtractionResult:
    """
    Result of extracting a UserProfile from a resume.
    """

    user_profile: UserProfile
    extraction_metadata: dict[str, Any]
    used_cache: bool
    resume_text_output_file: Path
    extracted_user_profile_file: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "extraction_metadata": self.extraction_metadata,
            "user_profile": self.user_profile.to_dict(),
        }


class UserProfileExtractorClient:
    """
    OpenAI-compatible LLM client for Resume -> UserProfile extraction.
    """

    def __init__(
        self,
        provider: str,
        api_key: str,
        base_url: str,
        model: str,
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

        if not self.api_key or self.api_key.startswith("your_"):
            raise ResumeIntakeError(
                "LLM_API_KEY is missing. Add your real LLM API key to .env "
                "before running resume extraction."
            )

        try:
            from openai import OpenAI
        except ImportError as error:
            raise ResumeIntakeError(
                "UserProfile extraction requires openai. "
                "Install dependencies with: pip install -r requirements.txt"
            ) from error

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )

    def extract_user_profile(
        self,
        resume_text: str,
        source_file: Path,
        resume_hash: str,
    ) -> UserProfile:
        """
        Extract a structured UserProfile from resume text.
        """

        prompt = _build_user_profile_extraction_prompt(
            resume_text=resume_text,
            source_file=source_file,
        )

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You extract structured user profile data from resumes. "
                        "Return only valid JSON. Do not use markdown. "
                        "Do not invent facts that are not present in the resume."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            stream=False,
        )

        response_text = _extract_llm_response_text(response)

        if response_text is None or not response_text.strip():
            raise ResumeIntakeError(
                "LLM returned an empty UserProfile response.\n"
                f"{_build_empty_response_debug_info(response, self)}"
            )

        print(
            "LLM UserProfile response debug: "
            f"length={len(response_text)}, preview={response_text[:300]!r}"
        )

        json_text = _normalize_json_response_text(response_text)

        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError as error:
            raise ResumeIntakeError(
                "UserProfile extraction returned invalid JSON after cleanup: "
                f"{json_text}"
            ) from error

        user_profile_payload = parsed.get("user_profile", parsed)

        if not isinstance(user_profile_payload, dict):
            raise ResumeIntakeError(
                "UserProfile extraction response did not contain a JSON object."
            )

        user_profile = UserProfile.from_dict(user_profile_payload)
        user_profile.raw_resume_text = resume_text
        user_profile.metadata.update(
            {
                "source": "resume_llm_extraction",
                "source_file": str(source_file),
                "resume_hash": resume_hash,
                "provider": self.provider,
                "model": self.model,
            }
        )

        analysis_notes = parsed.get("analysis_notes")
        inferred_notes = parsed.get("inferred_notes")

        if analysis_notes is not None:
            user_profile.metadata["analysis_notes"] = analysis_notes

        if inferred_notes is not None:
            user_profile.metadata["inferred_notes"] = inferred_notes

        return user_profile


def extract_resume_text(file_path: Path) -> str:
    """
    Extract text from a supported resume file.
    """

    file_path = Path(file_path)

    if not file_path.exists():
        raise ResumeIntakeError(f"Resume file not found: {file_path}")

    if not file_path.is_file():
        raise ResumeIntakeError(f"Resume path is not a file: {file_path}")

    extension = file_path.suffix.lower()

    if extension == ".pdf":
        text = _extract_pdf_text(file_path)
    elif extension == ".docx":
        text = _extract_docx_text(file_path)
    elif extension in {".txt", ".md"}:
        text = file_path.read_text(encoding="utf-8")
    else:
        supported = ", ".join(sorted(SUPPORTED_RESUME_EXTENSIONS))
        raise ResumeIntakeError(
            f"Unsupported resume extension '{extension}'. "
            f"Supported extensions: {supported}"
        )

    normalized_text = _normalize_resume_text(text)

    if not normalized_text:
        raise ResumeIntakeError(
            f"No text could be extracted from resume file: {file_path}"
        )

    return normalized_text


def generate_user_profile_from_resume(
    resume_file: Path = DEFAULT_RESUME_FILE,
    resume_text_output_file: Path = RESUME_TEXT_OUTPUT_FILE,
    extracted_user_profile_file: Path = EXTRACTED_USER_PROFILE_FILE,
    force_refresh: bool = USER_PROFILE_EXTRACTION_FORCE_REFRESH,
    client: UserProfileExtractorClient | None = None,
) -> UserProfileExtractionResult:
    """
    Extract resume text and generate a structured UserProfile.
    """

    resume_file = Path(resume_file)
    resume_text = extract_resume_text(resume_file)
    save_text(resume_text, resume_text_output_file)

    resume_hash = _hash_text(resume_text)
    cache_key = _build_cache_key(resume_hash=resume_hash)

    if not force_refresh:
        cached_result = _load_cached_extraction(
            extracted_user_profile_file=extracted_user_profile_file,
            cache_key=cache_key,
        )

        if cached_result is not None:
            print(
                "Loaded extracted UserProfile from cache: "
                f"{extracted_user_profile_file}"
            )
            return UserProfileExtractionResult(
                user_profile=cached_result["user_profile"],
                extraction_metadata=cached_result["extraction_metadata"],
                used_cache=True,
                resume_text_output_file=resume_text_output_file,
                extracted_user_profile_file=extracted_user_profile_file,
            )

    print("Calling LLM to extract UserProfile from resume.")

    extraction_client = client or UserProfileExtractorClient(
        provider=LLM_PROVIDER,
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        model=USER_PROFILE_EXTRACTION_MODEL,
    )

    user_profile = extraction_client.extract_user_profile(
        resume_text=resume_text,
        source_file=resume_file,
        resume_hash=resume_hash,
    )

    extraction_metadata = _build_extraction_metadata(
        resume_hash=resume_hash,
        cache_key=cache_key,
        source_file=resume_file,
        provider=extraction_client.provider,
        model=extraction_client.model,
    )

    result = UserProfileExtractionResult(
        user_profile=user_profile,
        extraction_metadata=extraction_metadata,
        used_cache=False,
        resume_text_output_file=resume_text_output_file,
        extracted_user_profile_file=extracted_user_profile_file,
    )

    save_extracted_user_profile(
        result=result,
        output_path=extracted_user_profile_file,
    )

    print(f"Saved extracted UserProfile to: {extracted_user_profile_file}")

    return result


def save_extracted_user_profile(
    result: UserProfileExtractionResult,
    output_path: Path = EXTRACTED_USER_PROFILE_FILE,
) -> Path:
    """
    Save the metadata wrapper and inner UserProfile for review.
    """

    return save_json(
        data=result.to_dict(),
        output_path=output_path,
    )


def load_extracted_user_profile(
    extracted_user_profile_file: Path = EXTRACTED_USER_PROFILE_FILE,
) -> UserProfile:
    """
    Load the inner UserProfile from an extracted profile wrapper.
    """

    payload = load_json(extracted_user_profile_file)
    user_profile_payload = _extract_user_profile_payload(payload)

    return UserProfile.from_dict(user_profile_payload)


def promote_extracted_profile_to_input(
    extracted_user_profile_file: Path = EXTRACTED_USER_PROFILE_FILE,
    user_profile_file: Path = USER_PROFILE_FILE,
    overwrite: bool = False,
) -> Path:
    """
    Promote reviewed extracted profile data into inputs/user_profile.json.
    """

    if user_profile_file.exists() and not overwrite:
        raise ResumeIntakeError(
            f"Refusing to overwrite existing user profile: {user_profile_file}. "
            "Run promotion with overwrite=True only after reviewing the "
            "extracted profile."
        )

    user_profile = load_extracted_user_profile(extracted_user_profile_file)

    if user_profile_file.exists() and overwrite:
        backup_path = user_profile_file.with_suffix(
            f"{user_profile_file.suffix}.bak"
        )
        shutil.copy2(user_profile_file, backup_path)
        print(f"Backed up existing user profile to: {backup_path}")

    promoted_path = save_json(
        data=user_profile,
        output_path=user_profile_file,
    )

    print(f"Promoted extracted UserProfile to: {promoted_path}")

    return promoted_path


def _extract_pdf_text(file_path: Path) -> str:
    """
    Extract text from a PDF resume.
    """

    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise ResumeIntakeError(
            "PDF resume extraction requires pypdf. "
            "Install dependencies with: pip install -r requirements.txt"
        ) from error

    reader = PdfReader(str(file_path))
    page_texts = [
        page.extract_text() or ""
        for page in reader.pages
    ]

    return "\n\n".join(page_texts)


def _extract_docx_text(file_path: Path) -> str:
    """
    Extract text from a DOCX resume.
    """

    try:
        from docx import Document
    except ImportError as error:
        raise ResumeIntakeError(
            "DOCX resume extraction requires python-docx. "
            "Install dependencies with: pip install -r requirements.txt"
        ) from error

    document = Document(str(file_path))
    paragraph_texts = [
        paragraph.text
        for paragraph in document.paragraphs
        if paragraph.text.strip()
    ]

    return "\n".join(paragraph_texts)


def _normalize_resume_text(text: str) -> str:
    """
    Normalize extracted resume text while preserving readable line breaks.
    """

    lines = [
        line.strip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]

    normalized_lines: list[str] = []
    previous_blank = False

    for line in lines:
        is_blank = line == ""

        if is_blank and previous_blank:
            continue

        normalized_lines.append(line)
        previous_blank = is_blank

    return "\n".join(normalized_lines).strip()


def _hash_text(text: str) -> str:
    """
    Build a stable SHA-256 hash for resume text.
    """

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _build_cache_key(resume_hash: str) -> str:
    """
    Build the cache key from resume, prompt, and schema versions.
    """

    cache_payload = {
        "resume_hash": resume_hash,
        "prompt_version": USER_PROFILE_EXTRACTION_PROMPT_VERSION,
        "schema_version": USER_PROFILE_EXTRACTION_SCHEMA_VERSION,
    }

    canonical_payload = json.dumps(
        cache_payload,
        ensure_ascii=False,
        sort_keys=True,
    )

    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


def _build_extraction_metadata(
    resume_hash: str,
    cache_key: str,
    source_file: Path,
    provider: str,
    model: str,
) -> dict[str, Any]:
    """
    Build metadata for the extracted profile wrapper.
    """

    return {
        "schema_version": USER_PROFILE_EXTRACTION_SCHEMA_VERSION,
        "prompt_version": USER_PROFILE_EXTRACTION_PROMPT_VERSION,
        "resume_hash": resume_hash,
        "cache_key": cache_key,
        "source_file": str(source_file),
        "provider": provider,
        "model": model,
        "extracted_at": utc_now_iso(),
    }


def _load_cached_extraction(
    extracted_user_profile_file: Path,
    cache_key: str,
) -> dict[str, Any] | None:
    """
    Load cached extraction when the cache key still matches.
    """

    if not extracted_user_profile_file.exists():
        return None

    payload = load_json(extracted_user_profile_file)

    if not isinstance(payload, dict):
        return None

    extraction_metadata = payload.get("extraction_metadata", {})

    if not isinstance(extraction_metadata, dict):
        return None

    if extraction_metadata.get("cache_key") != cache_key:
        return None

    user_profile_payload = _extract_user_profile_payload(payload)

    return {
        "extraction_metadata": extraction_metadata,
        "user_profile": UserProfile.from_dict(user_profile_payload),
    }


def _extract_user_profile_payload(payload: Any) -> dict[str, Any]:
    """
    Return the inner user_profile object from a wrapper or direct profile JSON.
    """

    if not isinstance(payload, dict):
        raise ResumeIntakeError("Extracted UserProfile file is not a JSON object.")

    user_profile_payload = payload.get("user_profile", payload)

    if not isinstance(user_profile_payload, dict):
        raise ResumeIntakeError(
            "Extracted UserProfile file does not contain a user_profile object."
        )

    return user_profile_payload


def _extract_llm_response_text(response: Any) -> str | None:
    """
    Extract message content from an OpenAI-compatible chat completion response.
    """

    choices = getattr(response, "choices", None)

    if not choices:
        return None

    first_choice = choices[0]
    message = getattr(first_choice, "message", None)

    if message is None:
        return None

    content = getattr(message, "content", None)

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        text_parts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict)
        ]
        return "".join(text_parts)

    return None


def _build_empty_response_debug_info(
    response: Any,
    client: UserProfileExtractorClient,
) -> str:
    """
    Build useful debug context for empty LLM responses.
    """

    choices = getattr(response, "choices", None)

    if hasattr(response, "model_dump_json"):
        raw_response = response.model_dump_json(indent=2)
    else:
        raw_response = repr(response)

    return (
        f"provider: {client.provider}\n"
        f"model: {client.model}\n"
        f"response_has_choices: {bool(choices)}\n"
        f"raw_response: {raw_response}"
    )


def _normalize_json_response_text(response_text: str) -> str:
    """
    Strip whitespace, remove markdown fences, and recover a JSON object.
    """

    stripped_text = response_text.strip()
    fenced_match = re.search(
        r"```(?:json)?\s*(.*?)\s*```",
        stripped_text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if fenced_match is not None:
        stripped_text = fenced_match.group(1).strip()

    if stripped_text.startswith("{") and stripped_text.endswith("}"):
        return stripped_text

    extracted_object = _extract_first_top_level_json_object(stripped_text)

    if extracted_object is not None:
        return extracted_object

    return stripped_text


def _extract_first_top_level_json_object(text: str) -> str | None:
    """
    Extract the first balanced top-level JSON object from surrounding text.
    """

    start_index = text.find("{")

    if start_index == -1:
        return None

    depth = 0
    in_string = False
    is_escaped = False

    for index in range(start_index, len(text)):
        character = text[index]

        if in_string:
            if is_escaped:
                is_escaped = False
            elif character == "\\":
                is_escaped = True
            elif character == '"':
                in_string = False
            continue

        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1

            if depth == 0:
                return text[start_index:index + 1]

    return None


def _build_user_profile_extraction_prompt(
    resume_text: str,
    source_file: Path,
) -> str:
    """
    Build the strict JSON extraction prompt.
    """

    return f"""
Extract a structured UserProfile from the resume text below.

Rules:
- Return only valid JSON.
- Return JSON only.
- Do not use markdown.
- Do not include explanations.
- Do not include commentary or code fences.
- The response must start with {{ and end with }}.
- Do not invent facts that are not explicitly present in the resume.
- If a field is missing, use an empty string, empty list, or metadata note.
- Put inferred observations in metadata.analysis_notes or inferred_notes.
- Do not put inferred items into education or work_experience as facts.
- Preserve factual resume fields in the resume's original language when useful.
- The resume may be fully Chinese; do not force main factual fields into English.
- Chinese factual fields should remain useful for Chinese internet monitoring.
- Add English-normalized summaries and bilingual search keywords in metadata for overseas opportunity search.
- If a translation or normalization is uncertain, mark it as approximate in metadata instead of treating it as a resume fact.
- Keep raw_resume_text as an empty string; the program will attach it exactly.

Return JSON with this shape:
{{
  "user_profile": {{
    "profile_id": "resume_profile",
    "name": "",
    "background_summary": "",
    "education": [
      {{
        "school": "",
        "degree": "",
        "field": "",
        "graduation_year": null,
        "notes": ""
      }}
    ],
    "work_experience": [
      {{
        "organization": "",
        "role": "",
        "start_date": "",
        "end_date": "",
        "description": "",
        "highlights": []
      }}
    ],
    "skills": [],
    "interests": [],
    "preferred_locations": [],
    "preferred_roles": [],
    "constraints": [],
    "raw_resume_text": "",
    "metadata": {{
      "source_language": "zh",
      "target_search_languages": ["zh", "en"],
      "english_background_summary": "",
      "search_keywords_zh": [],
      "search_keywords_en": [],
      "role_keywords_zh": [],
      "role_keywords_en": [],
      "education_normalized_en": [],
      "skills_normalized_en": [],
      "translation_notes": [],
      "analysis_notes": [],
      "inferred_notes": []
    }}
  }},
  "analysis_notes": [],
  "inferred_notes": []
}}

Source file:
{source_file}

Resume text:
{resume_text}
""".strip()


def main() -> None:
    """
    CLI entrypoint for explicit Resume -> UserProfile extraction.
    """

    parser = argparse.ArgumentParser(
        description="Extract a structured UserProfile from a resume.",
    )
    parser.add_argument(
        "--resume-path",
        type=Path,
        default=DEFAULT_RESUME_FILE,
        help="Path to resume file. Defaults to inputs/resume/resume.pdf.",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Ignore cached extraction and call the LLM again.",
    )
    parser.add_argument(
        "--promote",
        action="store_true",
        help="After extraction, promote the reviewed profile to inputs/user_profile.json.",
    )
    parser.add_argument(
        "--promote-only",
        action="store_true",
        help="Promote the existing extracted profile without running extraction.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow promotion to overwrite inputs/user_profile.json after review.",
    )

    args = parser.parse_args()

    ensure_project_directories()

    if args.promote_only:
        promote_extracted_profile_to_input(overwrite=args.overwrite)
        return

    result = generate_user_profile_from_resume(
        resume_file=args.resume_path,
        force_refresh=(
            args.force_refresh or USER_PROFILE_EXTRACTION_FORCE_REFRESH
        ),
    )

    print(f"Resume text saved to: {result.resume_text_output_file}")
    print(f"Extracted profile saved to: {result.extracted_user_profile_file}")
    print(f"Used cache: {result.used_cache}")
    print("inputs/user_profile.json was not modified.")

    if args.promote:
        promote_extracted_profile_to_input(overwrite=args.overwrite)


if __name__ == "__main__":
    main()
