# define predictable naming pattern for pr and branch
BRANCH_NAME_PREFIX="ci-reqs-bot/reqs-update-"
PR_NAME_PREFIX="[dnm] chore(tests): bot recompile requirements files for commit "
HEAD_HASH="$(git rev-parse HEAD)"
BRANCH_NAME="${BRANCH_NAME_PREFIX}${HEAD_HASH}"
PR_NAME="${PR_NAME_PREFIX}${HEAD_HASH}"

git checkout -B "$BRANCH_NAME"
git add -A
git commit -m "recompile riot requirements"
git push origin "$BRANCH_NAME"

found_prs="$(gh search prs is:open \"$PR_NAME_PREFIX\" in:title --json state,id,title --repo DataDog/dd-trace-py)"
found_pr="$(echo "${found_prs}" | jq -r '.[0].id')"
if [[ -z "$found_pr" || "$found_pr" = "null" ]]
then
    gh pr create --base "1.x" --title "$PR_NAME" --body "This is just a test" --repo "DataDog/dd-trace-py"
fi

found_prs="$(gh search prs is:open \"$PR_NAME_PREFIX\" in:title --json state,id,title --repo DataDog/dd-trace-py)"
found_pr_id="$(echo "${found_prs}" | jq -r '.[0].id')"
echo $found_pr_id

# figure out whether branch exists
# if it doesn't, create it
# pull branch
# commit local changes to it
# push
# figure out whether pr exists
# if not, create it
