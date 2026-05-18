def test_public_imports():
    from six2one.e621 import (
        E621Client,
        E621Error,
        E621APIError,
        E621AuthError,
        E621PermissionError,
        E621RateLimitError,
        E621NotFoundError,
        Post,
        Comment,
        Note,
        NoteVersion,
        PostFlag,
        PostEvent,
        PostVersion,
        PostApproval,
        Pool,
        PoolVersion,
        PostSet,
        PostReplacement,
        Favorite,
        PostVote,
        User,
        Artist,
        ArtistUrl,
        ArtistVersion,
        Export,
        TagExportRecord,
        TagAliasExportRecord,
        TagImplicationExportRecord,
        WikiPageExportRecord,
        PoolExportRecord,
        PostExportRecord,
    )

    assert E621Client
    assert Post
    assert Export
