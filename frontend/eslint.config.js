import tseslint from 'typescript-eslint'
import vue from 'eslint-plugin-vue'

export default [
  { ignores: ['dist/**', 'node_modules/**', 'coverage/**'] },
  ...tseslint.configs.recommended,
  ...vue.configs['flat/recommended'],
  { files: ['src/tests/**/*.ts'], rules: { 'vue/one-component-per-file': 'off' } },
  {
    files: ['**/*.vue'],
    languageOptions: { parserOptions: { parser: tseslint.parser, extraFileExtensions: ['.vue'] } },
    rules: {
      'vue/multi-word-component-names': 'off',
      'vue/max-attributes-per-line': ['error', { singleline: 6, multiline: 1 }],
      'vue/singleline-html-element-content-newline': 'off',
    },
  },
]
