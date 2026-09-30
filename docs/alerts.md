# Template Alert và Runbook

Mỗi alert phải dựa trên triệu chứng người dùng hoặc SLO, không dựa trực tiếp vào tên implementation nội bộ.

## Alert mẫu để tham khảo

Ví dụ dưới đây minh họa mức độ cụ thể cần có. Học viên không cần copy nguyên, nhưng ba alert trong bài nộp nên rõ ràng tương tự: điều kiện là gì, kéo dài bao lâu, ảnh hưởng tới user ra sao và người trực cần kiểm tra gì trước.

- Tên: `HighLatencyP95`
- Severity: `warning`
- Duration: `5m`
- Kênh thông báo: Slack `#k4-l3b-alerts`
- SLI/SLO liên quan: latency P95 của `response_sent.latency_ms`
- Điều kiện và thời gian duy trì: `p95(latency_ms) > 3000ms` trong 5 phút
- Ảnh hưởng tới người dùng: người dùng phải chờ lâu hơn trước khi nhận câu trả lời
- Ba bước kiểm tra đầu tiên:
  1. Mở dashboard latency để xác nhận P95/P99 và khoảng thời gian tăng.
  2. Lọc `data/logs.jsonl` trong khoảng đó, lấy một `correlation_id` có `latency_ms` cao.
  3. Mở trace cùng `correlation_id` trên Langfuse, so sánh các span chính để xác định bước nào bất thường.
- Mitigation tạm thời: dựa trên evidence thực tế để rollback prompt, khôi phục cấu hình liên quan, tắt practice scenario hoặc giảm tải khi demo.
- Owner: `student-<MSSV>`

## Alert 1

- Tên: `HighLatencyP95`
- Severity: `warning`
- Duration: `5m`
- Kênh thông báo: Slack `#k4-l3b-alerts`
- SLI/SLO liên quan: `fast_successful_requests` trong [config/slo.yaml](../config/slo.yaml); panel Latency của dashboard
- Điều kiện và thời gian duy trì: `p95(response_sent.latency_ms) > 2000ms` liên tục 5 phút. Đây là cảnh báo sớm, thấp hơn ngưỡng SLO 3000ms: baseline có request khởi động lạnh mất 1.6–2.9 giây (lấy prompt, khởi tạo SDK), phần lớn request còn lại 150–600ms tùy tracing bật hay tắt; khi bật practice `rag_slow`, latency_ms P95 đo được 2652ms, dưới SLO 3000ms nên SLO không phát hiện, còn ngưỡng 2000ms thì có.
- Ảnh hưởng tới người dùng: người dùng chờ lâu hơn 2 giây; nếu tiếp tục tăng qua 3 giây thì các request đó làm cạn error budget của SLO.
- Ba bước kiểm tra đầu tiên:
  1. Mở panel Latency, xác nhận P95/P99 tăng và khoảng thời gian bắt đầu; so với TTFT P95 để biết chậm trước hay sau token đầu tiên.
  2. Lọc `data/logs.jsonl` trong khoảng đó: `event == "response_sent"` và `latency_ms > 3000`, lấy một `correlation_id`.
  3. Mở trace cùng `correlation_id` trên Langfuse, so sánh span `retrieval` và `llm-generation`: span nào chiếm phần lớn thời gian là nơi cần xử lý.
- Mitigation tạm thời: nếu `retrieval` chậm, kiểm tra vector store/khôi phục cấu hình hoặc tắt scenario practice `rag_slow`; nếu `llm-generation` chậm, rollback label `production` của prompt về version trước; nếu tải tăng, giảm tải hoặc giới hạn concurrency.
- Owner: `student-2A202602378`

## Alert 2

- Tên: `HighErrorRateOrRetrievalFailure`
- Severity: `critical`
- Duration: `3m`
- Kênh thông báo: Slack `#k4-l3b-alerts`
- SLI/SLO liên quan: guardrail `error_rate_pct_max: 2` và `retrieval_success_rate_pct_min: 90` trong [config/slo.yaml](../config/slo.yaml); panel Errors của dashboard
- Điều kiện và thời gian duy trì: `count(request_failed) / count(request_received) * 100 > 2` HOẶC `count(tool_success == true) / count(tool_success != null) * 100 < 90`, liên tục 3 phút. Baseline: 0% lỗi, 100% retrieval success.
- Ảnh hưởng tới người dùng: request trả HTTP 500 hoặc không lấy được tài liệu để trả lời; đây là lỗi nhìn thấy trực tiếp nên đặt mức `critical` và duration ngắn hơn alert latency.
- Ba bước kiểm tra đầu tiên:
  1. Mở panel Errors: xem error rate, breakdown theo `error_type` và retrieval success để biết lỗi nằm ở tool hay ở app.
  2. Lọc `data/logs.jsonl` với `event == "request_failed"` (hoặc `tool_success == false`) trong khoảng đó, lấy `correlation_id` và đọc `error_type`.
  3. Mở trace cùng `correlation_id`: span `retrieval` có level `ERROR` và `status_message` cho biết lỗi (ví dụ `RuntimeError: Vector store timeout`).
- Mitigation tạm thời: khôi phục kết nối/cấu hình vector store, tắt scenario practice `tool_fail`, hoặc trả câu trả lời fallback không dùng retrieval trong lúc sửa; xác nhận error rate về dưới 2% trên dashboard.
- Owner: `student-2A202602378`

## Alert 3

- Tên: `CostPerRequestSpike`
- Severity: `warning`
- Duration: `10m`
- Kênh thông báo: Slack `#k4-l3b-alerts`
- SLI/SLO liên quan: guardrail `daily_cost_usd_max: 2.5` trong [config/slo.yaml](../config/slo.yaml); panel Cost và Tokens của dashboard
- Điều kiện và thời gian duy trì: `avg(response_sent.cost_usd) > 0.004` (khoảng 2 lần baseline 0.002 USD/request; bật practice `cost_spike` đo được khoảng 0.0066) HOẶC `sum(response_sent.cost_usd)` trong 1 giờ `> 2.5`, liên tục 10 phút. Điều kiện đầu bắt cost mỗi request tăng khi traffic thấp; điều kiện sau bắt tổng chi phí vượt ngân sách.
- Ảnh hưởng tới người dùng: người dùng không thấy lỗi nhưng chi phí vận hành tăng nhanh; nếu không xử lý sẽ vượt ngân sách trước khi ai đó nhận ra.
- Ba bước kiểm tra đầu tiên:
  1. Mở panel Cost và Tokens; nếu traffic không đổi mà cost tăng, xem `tokens_out` hay `tokens_in` tăng.
  2. Lọc `data/logs.jsonl` với `event == "response_sent"` có `cost_usd` hoặc `tokens_out` cao bất thường, lấy `correlation_id`.
  3. Mở trace cùng `correlation_id`, xem span `llm-generation`: usage output, cost và version prompt (`prompt_version`) để biết prompt mới có sinh câu trả lời dài hơn không.
- Mitigation tạm thời: rollback label `production` về version prompt trước, giới hạn `max_tokens`/độ dài câu trả lời, tắt scenario practice `cost_spike`; theo dõi `avg(cost_usd)` về gần baseline.
- Owner: `student-2A202602378`
