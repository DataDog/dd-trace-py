module.exports = {
  extends: ["@commitlint/config-conventional"],
  ignores: [
    /*
     * dependabot cannot be configured to match commitlint configuration
     * https://github.com/dependabot/dependabot-core/issues/1666
     */
    (commit) => commit.includes("chore(deps): Bump"),
  ],
};
