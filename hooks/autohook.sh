#!/usr/bin/env bash

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
    )

    repo_root=$(git rev-parse --show-toplevel)
    hooks_dir="$repo_root/.git/hooks"
    autohook_linktarget="../../hooks/autohook.sh"
    for hook_type in "${hook_types[@]}"
    do
        hook_symlink="$hooks_dir/$hook_type"
        ln -sf $autohook_linktarget $hook_symlink
    done
}


main() {
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
            for file in "${files[@]}"
            do
                scriptname=$(basename $file)
                echo "BEGIN $scriptname"
                eval "\"$file\""
                script_exit_code="$?"
                if [[ "$script_exit_code" != 0 ]]
                then
                  hook_exit_code=$script_exit_code
                fi
                echo "FINISH $scriptname"
            done
            if [[ $hook_exit_code != 0 ]]
            then
              echo "A $hook_type script yielded negative exit code $hook_exit_code"
              exit $hook_exit_code
            fi
        fi
    fi
}


main "$@"
