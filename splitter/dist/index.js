/**
 * QwenPaw 分段回复插件 —— Console 设置页
 *
 * 这是一个免构建的 ES Module：React / antd 由宿主（window.QwenPaw.host）提供，
 * 因此不需要 npm install，也不需要 vite 打包，改完直接覆盖即可生效。
 *
 * 注册内容：
 *   - 设置菜单（location: primary.settings）
 *   - 页面路由 /settings/splitter
 * 配置读写走后端插件暴露的 /api/splitter/* 接口。
 *
 * v1.0.1：修复「改了参数保存不生效 / 刷新后回到默认值」，以及保存时假提示"已保存"。
 * 宿主的 QP.host.fetch 内部会先过 getApiUrl()，等价于再补一个 /api；
 * 再传 "/api/splitter/config" 会被拼成 "/api/api/splitter/config"
 * → GET 404（页面只能显示默认值）、PUT 405（写入失败，却被当成成功提示）。
 * 这里按宿主规则自动选择正确写法，并留了 404/405 重试兜底；
 * 同时让 request() 真正检查响应状态码，失败时给出明确原因而不是"已保存"。
 */

(function () {
  var QP = window.QwenPaw;
  if (!QP || !QP.host || !QP.host.React) {
    return;
  }

  var React = QP.host.React;
  var antd = QP.host.antd || {};
  var h = React.createElement;

  var PLUGIN_ID = "splitter";

  var REL_API = "/splitter"; // 不带 /api 前缀（宿主 fetch 会自己补）
  var FULL_API = "/api" + REL_API; // 完整路径（原生 fetch 用）

  var USE_HOST_FETCH = !!(QP.host && typeof QP.host.fetch === "function");

  // 探测宿主是否会补 /api：getApiUrl("/x") 返回 "/api/x" 说明会自动补
  var HOST_ADDS_PREFIX = (function () {
    try {
      if (QP.host && typeof QP.host.getApiUrl === "function") {
        var probe = QP.host.getApiUrl("/__splitter_probe__");
        return typeof probe === "string" && probe.indexOf("/api") === 0;
      }
    } catch (err) {
      /* 探测失败时按默认处理 */
    }
    return true; // 有 host.fetch 的宿主默认会补 /api（当前实现如此）
  })();

  var API = USE_HOST_FETCH && HOST_ADDS_PREFIX ? REL_API : FULL_API;
  // 兜底写法：万一上面判断反了，遇到 404/405 时换一种再试一次
  var API_ALT = API === REL_API ? FULL_API : USE_HOST_FETCH ? REL_API : null;

  var Card = antd.Card;
  var Switch = antd.Switch;
  var Input = antd.Input;
  var TextArea = Input && Input.TextArea ? Input.TextArea : Input;
  var InputNumber = antd.InputNumber;
  var Select = antd.Select;
  var Button = antd.Button;
  var Space = antd.Space || function () { return null; };
  var Typography = antd.Typography || {};
  var Divider = antd.Divider;
  var Tag = antd.Tag;
  var Alert = antd.Alert;

  var DEFAULTS = {
    enabled: true,
    only_llm_result: true,
    interval_method: "random",
    interval: "1.5,3.5",
    log_base: 10,
    max_length: 1000,
    split_mode: "regex",
    split_regex: ".+?[。？！~…]+|.+$",
    split_length: 100,
    fallback_to_sentence: true,
    filter_regex: "",
    min_segment_length: 2,
    max_segments: 20,
    protect_code_blocks: true,
    exclude_channels: ["console"],
  };

  var SAMPLE =
    "今天天气挺好的。你要不要出去走走？我刚查了一下，周末都是晴天！" +
    "对了，记得带伞，万一下雨呢~";

  function callFetch(url, options) {
    if (USE_HOST_FETCH) {
      return QP.host.fetch(url, options || {});
    }
    return window.fetch(url, options || {});
  }

  function request(path, options) {
    function attempt(url) {
      return callFetch(url, options).then(function (resp) {
        return resp.text().then(function (text) {
          var data = null;
          try {
            data = JSON.parse(text);
          } catch (err) {
            data = null;
          }
          if (!resp.ok) {
            var detail =
              data && data.detail ? data.detail : String(text || "").slice(0, 120);
            throw new Error(
              "HTTP " + resp.status + " " + detail + "（地址：" + url + "）",
            );
          }
          return data || {};
        });
      });
    }
    return attempt(API + path).catch(function (err) {
      // 路径被宿主二次拼接（404/405）时，换另一种写法再试一次
      if (API_ALT && /HTTP (?:404|405)\b/.test(err.message)) {
        return attempt(API_ALT + path);
      }
      throw err;
    });
  }

  /* ------------------------------------------------------------------ */
  /* 小组件                                                              */
  /* ------------------------------------------------------------------ */

  function Row(props) {
    var title = props.title;
    var desc = props.desc;
    var children = props.children;
    return h(
      "div",
      {
        style: {
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          gap: "24px",
          padding: "12px 0",
          borderBottom: "1px solid rgba(128,128,128,0.15)",
        },
      },
      h(
        "div",
        { style: { flex: "1 1 auto", minWidth: 0 } },
        h(
          "div",
          { style: { fontWeight: 600, marginBottom: "4px" } },
          title,
        ),
        desc
          ? h(
              "div",
              {
                style: {
                  fontSize: "12px",
                  opacity: 0.65,
                  lineHeight: "1.6",
                  whiteSpace: "pre-wrap",
                },
              },
              desc,
            )
          : null,
      ),
      h(
        "div",
        { style: { flex: "0 0 auto", minWidth: "220px", textAlign: "right" } },
        children,
      ),
    );
  }

  function Title(props) {
    return h(
      "div",
      { style: { fontSize: "15px", fontWeight: 700, margin: "18px 0 4px" } },
      props.children,
    );
  }

  /* ------------------------------------------------------------------ */
  /* 设置页                                                              */
  /* ------------------------------------------------------------------ */

  function SplitterSettings() {
    var state = React.useState(null);
    var cfg = state[0];
    var setCfg = state[1];

    var statusState = React.useState("");
    var status = statusState[0];
    var setStatus = statusState[1];

    var savingState = React.useState(false);
    var saving = savingState[0];
    var setSaving = savingState[1];

    var textState = React.useState(SAMPLE);
    var previewText = textState[0];
    var setPreviewText = textState[1];

    var segState = React.useState([]);
    var segments = segState[0];
    var setSegments = segState[1];

    React.useEffect(function () {
      request("/config")
        .then(function (data) {
          setCfg(Object.assign({}, DEFAULTS, data.config || {}));
        })
        .catch(function (err) {
          setCfg(Object.assign({}, DEFAULTS));
          setStatus(
            "未能读取后端配置，显示的是默认值（" + err.message + "）",
          );
        });
    }, []);

    function update(patch) {
      var next = Object.assign({}, cfg, patch);
      setCfg(next);
      setSaving(true);
      request("/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      })
        .then(function (data) {
          setCfg(Object.assign({}, DEFAULTS, (data && data.config) || next));
          setStatus("已保存，立即生效（无需重启）");
        })
        .catch(function (err) {
          setStatus("保存失败：" + err);
        })
        .then(function () {
          setSaving(false);
        });
    }

    function runPreview() {
      request("/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: previewText }),
      })
        .then(function (data) {
          setSegments((data && data.segments) || []);
        })
        .catch(function (err) {
          setStatus("预览失败：" + err);
        });
    }

    if (!cfg) {
      return h("div", { style: { padding: "24px" } }, "加载中…");
    }

    var rows = [];

    rows.push(
      h(Row, {
        key: "enabled",
        title: "启用分段回复",
        desc: "开启后，智能体的长回复会被切成多条小句逐条发送。",
        children: h(Switch, {
          checked: !!cfg.enabled,
          onChange: function (v) {
            update({ enabled: !!v });
          },
        }),
      }),
    );

    rows.push(
      h(Row, {
        key: "only_llm",
        title: "仅对 LLM 结果分段",
        desc:
          "开启后，命令输出、系统提示、审批卡片等非大模型生成的文本保持整段发送。",
        children: h(Switch, {
          checked: !!cfg.only_llm_result,
          onChange: function (v) {
            update({ only_llm_result: !!v });
          },
        }),
      }),
    );

    rows.push(
      h(Row, {
        key: "method",
        title: "间隔方法",
        desc:
          "random：在下方区间内随机等待；\n" +
          "log：按字数取对数 y = log_base(x)，x 为字符数，y 为秒数；\n" +
          "fixed：固定等待区间最小值。",
        children: h(Select, {
          value: cfg.interval_method,
          style: { width: "100%" },
          onChange: function (v) {
            update({ interval_method: v });
          },
          options: [
            { value: "random", label: "random（随机间隔）" },
            { value: "log", label: "log（按字数取对数）" },
            { value: "fixed", label: "fixed（固定间隔）" },
          ],
        }),
      }),
    );

    rows.push(
      h(Row, {
        key: "interval",
        title: "随机间隔时间",
        desc: "格式：最小值,最大值（单位：秒）。例如 1.5,3.5",
        children: h(Input, {
          value: cfg.interval,
          onChange: function (e) {
            update({ interval: e.target.value });
          },
        }),
      }),
    );

    if (cfg.interval_method === "log") {
      rows.push(
        h(Row, {
          key: "log_base",
          title: "对数底数 log_base",
          desc: "y = log_base(x)，底数越大，等待越短。",
          children: h(InputNumber, {
            value: cfg.log_base,
            min: 1.1,
            step: 1,
            style: { width: "100%" },
            onChange: function (v) {
              update({ log_base: v || 10 });
            },
          }),
        }),
      );
    }

    rows.push(
      h(Row, {
        key: "maxlen",
        title: "分段回复字数阈值",
        desc:
          "只有字数不超过该值的消息才会被分段；\n" +
          "超过该值的消息将直接整段发送（不分段）。",
        children: h(InputNumber, {
          value: cfg.max_length,
          min: 0,
          step: 50,
          style: { width: "100%" },
          onChange: function (v) {
            update({ max_length: v == null ? 0 : v });
          },
        }),
      }),
    );

    rows.push(
      h(Row, {
        key: "mode",
        title: "分段模式",
        desc: "regex：按正则表达式切分；length：按固定长度；newline：按换行。",
        children: h(Select, {
          value: cfg.split_mode,
          style: { width: "100%" },
          onChange: function (v) {
            update({ split_mode: v });
          },
          options: [
            { value: "regex", label: "正则表达式" },
            { value: "length", label: "固定长度" },
            { value: "newline", label: "换行符" },
          ],
        }),
      }),
    );

    if (cfg.split_mode === "regex") {
      rows.push(
        h(Row, {
          key: "split_regex",
          title: "分段正则表达式",
          desc:
            "用于识别分段点，插件在「匹配结束位置」落刀。\n" +
            "推荐：.+?[。？！~…]+|.+$（注意是惰性的 .+?，\n" +
            "写成 .? 会被 .+$ 吞掉整行，导致只切出一段）",
          children: h(Input, {
            value: cfg.split_regex,
            onChange: function (e) {
              update({ split_regex: e.target.value });
            },
          }),
        }),
      );
    }

    if (cfg.split_mode === "length") {
      rows.push(
        h(Row, {
          key: "split_length",
          title: "每段字数",
          desc: "固定长度模式下，每个分段的字符数。",
          children: h(InputNumber, {
            value: cfg.split_length,
            min: 1,
            step: 10,
            style: { width: "100%" },
            onChange: function (v) {
              update({ split_length: v || 100 });
            },
          }),
        }),
      );
    }

    rows.push(
      h(Row, {
        key: "filter",
        title: "内容过滤正则表达式",
        desc:
          "从每个分段中移除命中的内容。\n" +
          "例如填 [。？！] 会去掉所有句号、问号、感叹号。留空则不处理。",
        children: h(Input, {
          value: cfg.filter_regex,
          placeholder: "留空",
          onChange: function (e) {
            update({ filter_regex: e.target.value });
          },
        }),
      }),
    );

    rows.push(
      h(Row, {
        key: "minseg",
        title: "短分段合并阈值",
        desc: "短于该字数的分段会并入上一段，避免出现「嗯。」这种碎片。",
        children: h(InputNumber, {
          value: cfg.min_segment_length,
          min: 0,
          step: 1,
          style: { width: "100%" },
          onChange: function (v) {
            update({ min_segment_length: v == null ? 0 : v });
          },
        }),
      }),
    );

    rows.push(
      h(Row, {
        key: "maxseg",
        title: "最多分段数",
        desc: "单条回复最多切成的段数，超出后尾部合并，防止刷屏。",
        children: h(InputNumber, {
          value: cfg.max_segments,
          min: 1,
          step: 1,
          style: { width: "100%" },
          onChange: function (v) {
            update({ max_segments: v || 1 });
          },
        }),
      }),
    );

    rows.push(
      h(Row, {
        key: "fallback",
        title: "正则失效时自动兜底",
        desc:
          "自定义正则没切出多段、但文本里明显有多个句末标点时，\n" +
          "自动退化为内置分句规则，保证「永远能分段」。",
        children: h(Switch, {
          checked: !!cfg.fallback_to_sentence,
          onChange: function (v) {
            update({ fallback_to_sentence: !!v });
          },
        }),
      }),
    );

    rows.push(
      h(Row, {
        key: "code",
        title: "保护代码块",
        desc: "不在 ``` 代码块内部切分，代码块整体附着在上一段末尾。",
        children: h(Switch, {
          checked: !!cfg.protect_code_blocks,
          onChange: function (v) {
            update({ protect_code_blocks: !!v });
          },
        }),
      }),
    );

    rows.push(
      h(Row, {
        key: "exclude",
        title: "排除的通道",
        desc:
          "这些通道不参与分段，多个用英文逗号分隔。\n" +
          "默认排除 console（网页控制台）。",
        children: h(Input, {
          value: (cfg.exclude_channels || []).join(","),
          onChange: function (e) {
            update({ exclude_channels: e.target.value.split(",") });
          },
        }),
      }),
    );

    var previewPanel = h(
      "div",
      { key: "preview", style: { marginTop: "18px" } },
      h(Title, null, "切分预览"),
      h(TextArea, {
        rows: 3,
        value: previewText,
        onChange: function (e) {
          setPreviewText(e.target.value);
        },
        style: { marginBottom: "8px" },
      }),
      h(
        Space,
        null,
        h(
          Button,
          { type: "primary", onClick: runPreview },
          "预览切分效果",
        ),
        segments.length
          ? h(Tag, { color: "blue" }, "共 " + segments.length + " 段")
          : null,
      ),
      segments.length
        ? h(
            "div",
            { style: { marginTop: "12px" } },
            segments.map(function (seg, index) {
              return h(
                "div",
                {
                  key: index,
                  style: {
                    padding: "8px 12px",
                    marginBottom: "6px",
                    borderRadius: "6px",
                    background: "rgba(128,128,128,0.1)",
                    fontSize: "13px",
                  },
                },
                h("b", null, "第 " + (index + 1) + " 段"),
                index > 0
                  ? h(
                      "span",
                      { style: { opacity: 0.7, marginLeft: "8px" } },
                      "（等待 " + Number(seg.delay).toFixed(2) + " 秒）",
                    )
                  : null,
                h("div", { style: { marginTop: "4px" } }, seg.text),
              );
            }),
          )
        : null,
    );

    return h(
      "div",
      { style: { padding: "24px", maxWidth: "860px" } },
      h(
        Typography.Title
          ? Typography.Title
          : "h2",
        { level: 4 },
        "分段回复",
      ),
      h(
        "div",
        { style: { fontSize: "12px", opacity: 0.6, marginBottom: "8px" } },
        "让智能体像人一样一小句一小句地发消息",
      ),
      status
        ? h(Alert, {
            message: status,
            type: /失败|错误/.test(status) ? "error" : "success",
            style: { marginBottom: "12px" },
            closable: true,
          })
        : null,
      h(Card, null, rows, previewPanel),
      h(
        "div",
        { style: { fontSize: "12px", opacity: 0.5, marginTop: "12px" } },
        "保存后立即生效；保存中：" + (saving ? "是" : "否"),
      ),
    );
  }

  /* ------------------------------------------------------------------ */
  /* 注册到宿主                                                          */
  /* ------------------------------------------------------------------ */

  try {
    if (QP.menu && QP.menu.add) {
      QP.menu.add(PLUGIN_ID, {
        id: "splitter.settings",
        label: "分段回复",
        route: "splitter.settings",
        location: "primary.settings",
        order: 100,
      });
    }
    if (QP.route && QP.route.add) {
      QP.route.add(PLUGIN_ID, {
        id: "splitter.settings",
        path: "/settings/splitter",
        component: SplitterSettings,
      });
    }
  } catch (err) {
    // 注册失败不能影响 Console 其它功能
    if (window.console) {
      window.console.error("[splitter] 注册设置页失败", err);
    }
  }
})();
