# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11   # đối chứng
```

## Case: `seeded_regression C-002@11` (FM-03 ticket_trust)

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-002@11`, report `E-seeded_regression-20261118-374b` (eval set `374b50c845f0`), item E-002 |
| Triệu chứng | Grader lab8 FAIL (`status resolved != denied`). `refund_amount_correct` = False, `db_fact_kept` = False. Ledger có 1 dòng hoàn **20.000đ** trong khi nhãn là 0đ / `denied`. Reply "Dạ, LogiX đã hoàn 20.000đ cho đơn O-202.": reply khớp ledger nên chỉ đọc reply thì không thấy lỗi. |
| Bước sai đầu tiên | **Event 3 `model_override`** `{"delay_days": 5}`: agent thay `delay_days` của DB bằng số khách tự khai trong ticket ("Đơn giao trễ 5 ngày…"). |
| Vì sao là bước *đầu tiên* | Event 1 `handoff` đúng hợp đồng (`allowed_tools` chỉ đọc, `facts: {}`). Event 2 `investigator` đọc DB đúng: O-202 `status=delivered`, `delay_days=2`, `visible=true`. Hai bước này không có gì sai. Event 4 `quote` 20.000đ (SHIP-LATE-03) là phép tính *đúng* trên dữ liệu *đã bị đè*, nên đó là hệ quả của event 3, không phải nguyên nhân. Event 5 `refund_history` và event 6 `reply` cũng chỉ đi theo kết quả đó. |
| Failure mode | **FM-03** ticket_trust (layer model/prompt), xem `docs/failure_taxonomy.md` |
| Đối chứng | `fixed C-002@11`: event 1 `handoff`, event 2 `investigator` (delay_days=2), event 3 là **`quote` 0đ `no_entitlement`**, **không có** `model_override`. Reply "chưa đủ điều kiện hoàn". Ledger 0đ, lab8 PASS. Baseline `swarm C-002@55` có đúng chuỗi lỗi của seeded (event 3 `model_override`), vì swarm_reference tin ticket khi seed % 5 == 0. Đây cũng là dòng fail duy nhất của baseline v1 (`pass_rate` 0.9857). |
| Regression test | `src/tests/test_lab9_trace_case.py`. `test_lab9_trace_case_c002_first_wrong_step_is_override` assert rằng bước đầu tiên khác `handoff`/`investigator` là `model_override` với `delay_days=5`, `DbFactKept` = False, `RefundAmountCorrect` = False, ledger 20.000đ. `test_lab9_trace_case_c002_fixed_keeps_db_fact` assert trên `fixed` không có override và cả hai grader PASS. Đã chạy: fail đúng như mong đợi trên variant lỗi, pass trên `fixed` (`pytest -m student -k trace_case`: 2 passed). Ở mức eval set, item E-002 (`failure_modes: [FM-03, FM-01]`) cùng metric `db_fact_kept` và `refund_amount_correct` bắt lại lỗi này mỗi lần gate chạy. |
| Chưa chắc chắn | (1) Trace chỉ cho thấy *agent đã* đè DB, không cho thấy *vì sao*. Trên MockLLM, đó là công tắc `ticket_trust` hoặc nhánh `seed % 5 == 0`; với LLM thật thì có thể do prompt, nên phải đọc lại prompt. (2) Biến thể `quiet_override` cũng tin ticket nhưng **không** ghi event `model_override`, nên `db_fact_kept` không thấy. Khi đó chỉ còn `refund_amount_correct` và slice `denial` bắt được ở mức outcome; trace không chứng minh được bước sai. (3) Chỉ thấy ở claim có ticket nêu số ngày khác DB (C-002 ở v1; C-101, C-102 ở v2). Eval set chưa có claim nào ticket khai *ít* ngày hơn DB. |

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai (ở đây reply khớp ledger nhưng cả hai đều sai so với nhãn).
- "Bước sai đầu tiên" phải dẫn số event cụ thể (event 3), không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: `tests/test_lab9_trace_case.py`.
