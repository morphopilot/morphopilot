import js from "@eslint/js";
import tseslint from "typescript-eslint";
import hooks from "eslint-plugin-react-hooks";
import globals from "globals";

export default tseslint.config(
  {ignores:["node_modules/**", "standalone/dist/**", "website/**", "site/**", "docs/**"]},
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {languageOptions:{globals:{...globals.browser,...globals.node}},rules:{"no-empty":["error",{allowEmptyCatch:true}]}},
  {files:["**/*.{ts,tsx}"],plugins:{"react-hooks":hooks},rules:{...hooks.configs.recommended.rules}},
);
