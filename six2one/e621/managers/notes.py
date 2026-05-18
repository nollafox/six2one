"""Note managers."""

from .base import SearchManager
from . import endpoints
from ..models import Note, NoteVersion


class NotesManager(SearchManager[Note]):
    """Manager for notes."""

    resource_name = "notes"
    model_type = Note
    index_endpoint = endpoints.NOTES_INDEX
    index_response_key = "notes"


class NoteVersionsManager(SearchManager[NoteVersion]):
    """Manager for note versions."""

    resource_name = "note_versions"
    model_type = NoteVersion
    index_endpoint = endpoints.NOTE_VERSIONS_INDEX
    index_response_key = "note_versions"
