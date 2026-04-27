Dont use hardcoded value.
    claude_code.py:64: return ["claude/global/", "claude/projects/"]
    config.py:143: valid_sync_modes = {"symlink", "copy"}
    git_utils.py:127: result = git("pull", "--rebase", "origin", "main", data_dir=data_dir)
    claude_code.py:180: if f.name == ".needs_mapping" or f.is_dir():
    claude_code.py:191
    
config.py:95: "if section == 'mesy':"? Why is there a check and skip for "mesy" in the projects.conf file?

Why are we creating a mapping dict[str, str] of [raw path, canonical] in load_project_mappings(), and then use _migrate_repo_to_canonical() to revert the dict to [canonical, rawpath] in canonical_raws? Why not make [canonical, rawpath] immediately in load_project_mappings() and only loop(line 140-149 in claude_code.py) through that one dict?