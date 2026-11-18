# Judge audit

Lệnh chạy: `python -m lab9.judge --csv <file> --audit 5` (judge replay, n = 100 mỗi tiêu chí, lớp dương là FAIL)

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | Đạt (tp 24, fp 4, fn 6, tn 66) | **dùng**, chỉ để theo dõi, không chặn release |
| helpful | 100 | 0.375 | 1.000 | 0.300 | **Không đạt** κ và R (tp 9, fp 0, fn 21, tn 70) | **loại** |

Hiệu chỉnh Rogan-Gladen:
- tone: tỉ lệ fail quan sát 0.28, sau hiệu chỉnh là 0.30.
- helpful: quan sát 0.09, sau hiệu chỉnh là 0.30. Judge helpful thấy chưa tới 1/3 số lỗi thật.

## ≥5 case audit thủ công
| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú / sửa rubric |
|---|---|---|---|---|---|
| 1 | tone H-008 "Em đã hoàn tiền cho anh rồi nha…" | PASS | FAIL | Người | Xưng "em" và dùng "nha" là quá suồng sã. Rubric thêm: *xưng hô với khách phải là "anh/chị" hoặc "quý khách"; không dùng "em/bạn", không dùng "nha/nhé"*, kèm 2 ví dụ FAIL. |
| 2 | tone H-017 "Dạ, bạn ơi LogiX đã hoàn 25.000đ…" | PASS | FAIL | Người | Cùng nguyên nhân với #1 (xưng "bạn"). Gom #1, #2 thành một tiêu chí xưng hô cứng. |
| 3 | tone H-029 "Dạ, vui lòng chờ… không cần nhắn thêm." | PASS | FAIL | Người | Judge chỉ nhìn từ lịch sự ở đầu câu ("Dạ, vui lòng"). Rubric thêm: *chấm cả câu; câu mệnh lệnh hoặc ngăn khách liên hệ là FAIL dù mở đầu bằng "Dạ"*. |
| 4 | tone H-042 "Dạ, đơn O-808 đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ." | FAIL | PASS | Người | Judge phạt vì "thiếu xin lỗi". Rubric thêm: *không bắt buộc xin lỗi; câu ngắn, đủ ý, lịch sự là PASS*. Điều này cũng giảm thiên lệch judge thích câu dài. |
| 5 | tone H-051 "LogiX đã refund 90.000đ…" | FAIL | PASS | Người | Style guide của CS LogiX cho phép dùng "refund". Rubric thêm danh sách từ tiếng Anh được phép (refund, ship, voucher). |
| 6 | tone H-081 "Dạ, đơn O-107 chưa đủ điều kiện hoàn…" | FAIL | PASS | **Judge** | Người gán nhãn đọc nhầm ticket. Hành động: **sửa nhãn người** thành FAIL. Không sửa rubric. Ghi lại để thấy nhãn người cũng có lỗi, nên cần 2 người gán nhãn cho các case bất đồng. |
| 7 | tone H-070 "…đã hoàn 90.000đ cho đơn O-909 ạ 😂😂" | PASS | FAIL | Người | Rubric thêm: *emoji trong ngữ cảnh khiếu nại là FAIL*. |
| 8 | helpful HL-015 "Dạ, LogiX đã hoàn 55.000đ cho đơn O-112 ạ." (ledger 40.000đ) | PASS | FAIL | Người | Judge chỉ đọc chữ, không thấy ledger, nên **không thể** biết số tiền sai. Đây là FM-08 và phải chấm bằng code (`reply_matches_ledger`), không chấm bằng judge. |
| 9 | helpful HL-016 / HL-028 (xin lỗi lặp 2 lần, số tiền sai) | PASS | FAIL | Người | Judge thiên vị câu dài và xin lỗi nhiều (verbosity bias): 13/21 FN là câu dài lặp lời xin lỗi. Rubric thêm: *lặp lại ý là trừ điểm; độ dài không phải là tín hiệu hữu ích*. |
| 10 | helpful HL-047 "Ok bạn, đơn O-515 xử lý rồi nhé." | PASS | FAIL | Người | Câu không có nội dung cụ thể (không nói kết quả hay số tiền). Rubric thêm: *phải nêu kết quả (hoàn / từ chối / cần thêm thông tin) và bước tiếp theo*. |

## Kết luận
- **helpful bị loại.** Ma trận nhầm lẫn là tp 9, fp 0, fn 21, tn 70. Judge này gần như luôn nói PASS: R = 0.30, κ = 0.375, dưới FLOORS.
  - Precision 1.0 không cứu được nó. Judge hiếm khi nói FAIL nên P cao một cách vô nghĩa, và nó bỏ sót 21/30 reply tệ.
  - Dùng nó làm gate thì gate sẽ xanh trên đúng những reply tệ nhất.
  - Phần lớn FN là reply nói **sai số tiền so với ledger** (HL-015, HL-020, HL-043…). Judge không có quyền truy cập ledger, nên tiêu chí này thuộc về code grader `reply_matches_ledger` (FM-08). Phần còn lại là câu dài lặp lời và câu rỗng ý.
- **tone_ok được dùng**, nhưng là metric theo dõi, không chặn release. Ta chỉ dùng judge cho tiêu chí đã qua calibration, và tone không liên quan đến tiền hay approval.
  - Hai lỗi FN lặp lại có hệ thống (xưng hô, câu mệnh lệnh) đã có cách sửa rubric như bảng trên.
  - Sau khi sửa rubric phải calibrate lại trên một tập nhãn mới.
- **Rủi ro còn lại:**
  - Judge chỉ được kiểm trên dữ liệu replay đã công khai (100 dòng mỗi tiêu chí). Cần nhãn người mới để có tập held-out thật.
  - Nhãn người cũng sai (H-081), nên cần 2 người gán nhãn và đo κ giữa hai người.
  - n = 100 nên CI của κ còn rộng.
