# 制作与审阅协议

本流程不绑定模型品牌。助手必须能访问输入、执行引擎、实际查看图片并交付文件。若只能读文本或看到代码，报告缺少哪项能力，不声称已看图。切换宿主工具时使用当前平台提供的文件/图片/执行能力，不假设工具名称相同。

## 启动与持续推进

先定位本技能目录。Mac/Linux用 `sh "<技能目录>/scripts/photobook.sh"`，Windows用平台执行工具运行 `powershell -NoProfile -File "<技能目录>/scripts/photobook.ps1"`。下文用`引擎`代表该入口，所有路径作为独立参数传入。

若宿主沙箱不允许用户级缓存，助手将PHOTOBOOK_CACHE设为当前获准工作区内、照片目录外的私有缓存绝对路径，并在后续调用中保持一致；不要求用户手动配置或扩大无关目录权限。组件正在被其他任务准备时稍后重试，不并发反复安装。

1. `引擎 doctor --source "照片目录" --prepare`。自动准备固定模型；检查运行环境。`host_vision_verified=false`表示宿主看图须实际验证，不能将其改成true冒充测试通过。
2. `引擎 start "照片目录" --output "结果目录/旅行名/Vn"`。返回内部`job`路径，后续使用它。选未占用版本，不覆盖历史。可用`--config`传UTF-8 JSON，页数须偶数；默认32页、约60张。没有偏好不逐项询问。
3. `引擎 advance "job路径"`，持续处理下述状态，直至发布。进度在磁盘里，中断后读取同一job继续，不新建重复任务。

| 状态 | 助手动作 |
| --- | --- |
| PROGRESS | 本小步完成，继续advance |
| NEEDS_VISION / NEEDS_LOCATION | 读取返回的task JSON，实际查看images中的所有图片；小字或表情不清时打开原图。按下节写答复，再submit-review |
| NEEDS_CORRECTION | 查看实际问题，revise对应环节后重做；不能直接将问题清空 |
| READY_TO_DELIVER | `引擎 deliver "job路径"` |
| COMPLETE | 交付返回的pdf、html和overview三个文件 |
| BLOCKED / 非零退出 | 根据具体错误修正；有限次失败后说明阻塞，不声称完成 |

答复保存在job内部，例如`job/answers/阶段-任务-轮次.json`，用`引擎 submit-review "job路径" "答复.json"`提交。然后继续advance。每任务至多三次提交；错误及旧答复保留。若已通过阶段需修正，用`引擎 revise "job路径" layout`等重新开启阶段；最多三轮。不要自行删除审阅响应、修改heads或伪造已通过快照。

## 视觉答复

每个任务答复均包含：`task_id`、`reviewer:{kind:"visual_model",name:"实际可知的模型或宿主标识",session:"本次会话标识"}`、`seen_photo_ids`、`seen_artifacts`、`evidence_note`。seen_artifacts填写task中的相对路径，seen_photo_ids只包含实际看过的本任务照片。不知道精确模型版本时注明“宿主未公开版本”，不编造型号。图片工具返回错误不算看过。

各阶段字段范例与实际约束在任务目录的`*.answer.example.json`和task中；范例不是已有结果。`test_fixture`仅允许合成测试，严禁用于用户照片。

- screen：每张target给keep/exclude。技术风险不等于不能用，保留唯一瞬间、夜景与有意义的虚化；明确不能用才排除。
- dedup：每张target给keep/merge。merge的related_photo_ids只给一个实际看过、最终保留的代表；同地点不同活动不能合并，不确定保留。
- cluster局部任务：events数组，每项event_id/title/photo_ids/reason，时间、地点和场景判断分组；成员恰好覆盖本批target_ids，边界参考图不重复分配。大批分段时，advance另给previous_cluster_events；若确为同一连续事件，在本批事件填continuation_of引用前序完整event_id，必须看边界并写延续理由，不强制每48张变成新事件。未知地点不能猜。
- event_select：逐张candidate/alternate/drop和hero/narrative/detail角色。优先事件代表、情感和互补信息，不全留同景连拍。普通事件1至3张只是起点。
- select：逐张select/alternate，附每事件coverage（event_id、selected_ids、reason）。在目标容量内保证事件覆盖和多样性；超限需quantity_reason。
- cluster全书任务（context.phase=global_events）：实际看全部事件代表图，event_groups填写event_id/title/source_event_ids/reason，source_event_ids恰好覆盖局部已审事件。纠正分批造成的碎片；同城市不同活动仍独立。需要拆分成员则revise cluster，不能凭代表图重排未看成员。
- story落选任务（context.phase=omission_check）：逐张target给retain_omitted/reconsider及具体原因，比较提供的入选代表。覆盖全部初筛后未入选图，包括近似合并图；reconsider不自动入选。
- story编排任务（context.phase=assembly）：selected、chapters（按selected顺序覆盖）、changes、coverage、summary。须重新查看原入选和建议补回图，补回只能来自本编排任务实际看过的照片。每个summary含text/photo_ids，地点需location_claims。不得虚构人物身份和内心活动。
- layout：pages、cover_photo_id。按task的editorial_v1策略安排页序，items逐张含photo_id/role/crop/reason。每页reason、简短title，必要才有caption和caption_reason；location/date独立。text_sources引用照片；地点用location_claims。遵循主辅与实际尺寸，不退回旧模板；不机械塞满等大四宫格。
- qa：实际查看page_images中的封面与每页；page_reviews逐页number（封面为"cover"）/status/reason，有issue则写fix，附unresolved。不能只读JSON就通过。回到layout修正后重新render和qa。

## 地点答复

locate任务给24张以内的照片，答复除共同看图信封外包含`records`和`sources`。每张record：

```json
{"photo_id":"任务中的ID","city":null,"city_status":"unknown","place":null,"place_status":"unknown","confidence":"low","kind":"unknown","visited":false,"label":"未确认拍摄地点","uncertainty":"没有足够位置证据","evidence":{"method":"检查EXIF及实际画面","observed":"填写实际看见的证据或缺失信息","source_ids":[],"related_photo_ids":[]}}
```

city_status/place_status可为confirmed、inferred、unknown；kind可为ground、rail、airborne、airport、unknown。confirmed名称需GPS加核实过的外部来源，或清晰可见招牌；不因最近POI就认定拍在那里。无GPS推断需关联证据照片，关联城市应已确认；跨任务资料可读已有答复核实，但任务seen_photo_ids仅本批。sources每项source_id/url/title/checked_at/note，source_id跨任务唯一。没有浏览工具或无法核实就保留unknown，不阻塞整书也不编地名。机上和列车沿途不计入到访城市。

## 输出与回溯

交付`照片书.pdf`、`照片书.html`与`跨页总览.jpg`三个成品。网页是本地离线文件；总览从最终合订PDF生成，包含封面和全部内页，按真实左右跨页排列并标注页码。无需用户额外要求总览。检查总览无漏页、错序或裁切；有问题修正后再交付。无需额外交付ZIP、说明、清单或其他缩略图。任务、逐张理由、地点证据、修正和发布哈希均在job内部；用户以后询问再读取。原照片只读，不移动、不覆盖。不能自动发布到公网。
