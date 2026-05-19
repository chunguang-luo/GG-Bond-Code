import chalk from "chalk";
import { Marked } from "marked";
import { markedTerminal } from "marked-terminal";

chalk.level = 3;

const marked = new Marked(
  markedTerminal({ showSectionPrefix: false })
);

const md = `| 功能 | 状态 |
|------|------|
| 表格 | ✅ |
`;

console.log(marked.parse(md));