#!/usr/bin/env bash
#  MIT License
#
#  Copyright (c) 2017 Nikola Kantar
#
#  Permission is hereby granted, free of charge, to any person obtaining a copy
#  of this software and associated documentation files (the "Software"), to deal
#  in the Software without restriction, including without limitation the rights
#  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#  copies of the Software, and to permit persons to whom the Software is
#  furnished to do so, subject to the following conditions:
#
#  The above copyright notice and this permission notice shall be included in all
#  copies or substantial portions of the Software.
#
#  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
#  SOFTWARE.

# Autohook
# A very, very small Git hook manager with focus on automation
# Contributors:   https://github.com/Autohook/Autohook/graphs/contributors
# Version:        2.3.0
# Website:        https://github.com/Autohook/Autohook


echo() {
    builtin echo "[Autohook] $@";
}


install() {
    hook_types=(
        "pre-commit"
        "post-merge"
        "post-checkout"
    )

    # Install into git-common-dir/hooks; a worktree's .git is a file.
    git_common_dir=$(cd "$(git rev-parse --git-common-dir)" && pwd)
    hooks_dir="$git_common_dir/hooks"
    mkdir -p "$hooks_dir"
    autohook_linktarget="../../hooks/autohook.sh"
    for hook_type in "${hook_types[@]}"
    do
        hook_symlink="$hooks_dir/$hook_type"
        ln -sf $autohook_linktarget $hook_symlink
    done

    drop_relative_hooks_path "$git_common_dir"
}


# Unset a relative core.hooksPath in repo-scoped config; it does not resolve in a worktree.
drop_relative_hooks_path() {
    git_common_dir="$1"
    git_dir=$(cd "$(git rev-parse --git-dir)" && pwd)
    # Repo-scoped files only; leave absolute / --global hooksPath alone.
    for scope_file in "$git_dir/config" "$git_common_dir/config"
    do
        [[ -f $scope_file ]] || continue
        configured=$(git config --file "$scope_file" --get core.hooksPath 2>/dev/null) || continue
        [[ -n $configured ]] || continue
        if [[ $configured == /* ]]
        then
            echo "core.hooksPath in $scope_file is '$configured'; leaving it alone."
            continue
        fi
        git config --file "$scope_file" --unset-all core.hooksPath
        echo "Removed relative core.hooksPath ('$configured') from $scope_file; it never resolves inside a worktree."
    done
}


main() {
    git config --local include.path ../.gitconfig
    calling_file=$(basename $0)

    if [[ $calling_file == "autohook.sh" ]]
    then
        command=$1
        if [[ $command == "install" ]]
        then
            install
        fi
    else
        repo_root=$(git rev-parse --show-toplevel)
        hook_type=$calling_file
        symlinks_dir="$repo_root/hooks/$hook_type"
        files=("$symlinks_dir"/*)
        number_of_symlinks="${#files[@]}"
        if [[ $number_of_symlinks == 1 ]]
        then
            if [[ "$(basename ${files[0]})" == "*" ]]
            then
                number_of_symlinks=0
            fi
        fi
        echo "Looking for $hook_type scripts to run...found $number_of_symlinks!"
        if [[ $number_of_symlinks -gt 0 ]]
        then
            hook_exit_code=0
            failed_scripts=()
            for file in "${files[@]}"
            do
                scriptname=$(basename $file)
                echo "BEGIN $scriptname"
                "$file" "$@"
                script_exit_code="$?"
                if [[ "$script_exit_code" != 0 ]]
                then
                    hook_exit_code=$script_exit_code
                    failed_scripts+=("$scriptname (exit $script_exit_code)")
                    echo "FAILED $scriptname — exited with code $script_exit_code"
                fi
                echo "FINISH $scriptname"
            done
            if [[ $hook_exit_code != 0 ]]
            then
              if [[ $hook_type == "pre-commit" || $hook_type == "commit-msg" ]]
              then
                echo ""
                echo "The following $hook_type hooks failed — aborting commit:"
                for s in "${failed_scripts[@]}"; do
                    echo "  x $s"
                done
                exit $hook_exit_code
              else
                echo "A $hook_type script exited with non-zero code $hook_exit_code (non-blocking, continuing)."
              fi
            fi
        fi
    fi
}


main "$@"
