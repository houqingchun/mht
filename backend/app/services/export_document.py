"""一份受控导出的产物：文件正文 **加上它用了哪些列**。

这个模块只有这一个 dataclass，存在的理由是**避开一个环**：`task_service` 要给
`task_completion_csv` 用它（那份 CSV 也是导出作业的产物），而 `export_service` 依赖
`assessment_service`、`assessment_service` 又依赖 `task_service`——把类放在
`export_service` 里，`task_service` 反过来 import 它就会成环。做法与
`care_events.py` 同一个形状：抽出一个**谁也不依赖**的小模块，让两个消费者都往下看。

三个字段一起返回，是为了让「这份文件里有什么」只有**一个**定义：表头是
`writer.writerow(header)` 写的那一行，而落进 `export_job.field_policy` 的是同一
个 `header`；`row_count` 是 `writer.writerow(row)` 被调用的次数，落进
`export_job.row_count`。各写一份的话，政策里记的列与文件里真实存在的列会在某次
改动之后分岔，而**两边看起来都对**——这正是 §16.3 那句话要防的（导出接口不得
接受任意字段名，只能从服务端白名单里选）。

`row_count` **不数表头**：一份 0 行的导出（范围内一个学生都没有）在界面上必须
说得出来，而把表头算进去时它会显示「1 行」。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExportDocument:
    csv_text: str
    columns: tuple[str, ...]
    row_count: int
