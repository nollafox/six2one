from __future__ import annotations

from pathlib import Path

from six2one._commands.text import Template


def display_path(path: Path) -> str:
    try:
        return "~/" + str(path.expanduser().relative_to(Path.home()))
    except ValueError:
        return str(path)


LIVE = Template(
    """
    six2one bootstrap

    Workspace
      Home                     {home}
      Storage                  {storage_path}
      Images                   {images_dir}

    {phase}
      {detail_1}
      {detail_2}
      {detail_3}
      {detail_4}
    """,
    missing="blank",
)

SUMMARY = Template(
    """
    Bootstrap complete.

    Summary
      Home                     {home}
      Storage                  {storage_path}
      Images                   {images_dir}
      Tag snapshot             {tag_snapshot}
      Tags                     {tags_count}
      Aliases                  {aliases_count}
      Implications             {implications_count}
      Closure rows             {closure_count}
      Changed                  {changed}

    Next
      Explain a query:
        621 query explain "dragon rating:s"

      Queue downloads:
        621 queue "dragon rating:s"
    """,
    missing="blank",
)

ALREADY_BOOTSTRAPPED = Template(
    """
    six2one is already bootstrapped.

    Workspace
      Home                     {home}
      Storage                  {storage_path}
      Images                   {images_dir}

    Tag database
      Status                   ready
      Snapshot                 {tag_snapshot}
      Tags                     {tags_count}

    Nothing was changed.
    """,
    missing="blank",
)
