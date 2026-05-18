from six2one.e621 import (
    Artist,
    ArtistUrl,
    ArtistVersion,
    Comment,
    E621APIError,
    E621AuthError,
    E621Client,
    E621Error,
    E621NotFoundError,
    E621PermissionError,
    E621RateLimitError,
    Export,
    Favorite,
    Note,
    NoteVersion,
    Pool,
    PoolExportRecord,
    PoolVersion,
    Post,
    PostApproval,
    PostEvent,
    PostExportRecord,
    PostFlag,
    PostReplacement,
    PostSet,
    PostVersion,
    PostVote,
    TagAliasExportRecord,
    TagExportRecord,
    TagImplicationExportRecord,
    User,
    WikiPageExportRecord,
)


def test_public_imports():
    assert E621Client
    assert Post
    assert Export
