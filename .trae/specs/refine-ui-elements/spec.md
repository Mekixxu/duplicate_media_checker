# 优化删除按钮文案及复选框点击范围 Spec

## Why
用户反馈“全局删除选中文件”文案可以优化为“删除全部选中文件”以更符合直觉。同时，现有的表格复选框点击范围太小，导致选中操作较为费劲，需要扩大点击区域以提升交互体验。

## What Changes
- 将模板和报告页面中出现的“全局删除选中文件”按钮文本修改为“删除全部选中文件”。
- 通过 CSS 扩大 Element Plus 表格中复选框的可点击区域，使其更容易被选中。

## Impact
- Affected specs: 无
- Affected code: `movie_manager/templates/report.html`, `movie_report.html`

## MODIFIED Requirements
### Requirement: 批量删除按钮文案
原有的全局批量删除按钮显示的文本应当从“全局删除选中文件”更新为“删除全部选中文件”。

### Requirement: 复选框交互体验
系统应当允许用户更容易地点击选中文件或分组的复选框，复选框及其父级容器的样式应被调整以扩大可点击热区。