# 接口与事件类型

在desktop目录执行`npm ci --prefix tools/api-generator`安装生成工具的锁定依赖，执行`npm run gen:api`生成HTTP类型、事件联合类型和运行时schema副本。`npm run check:api`仅在内存生成并比较文件，差异直接失败，不改写被检查文件。桌面TypeScript 6与生成工具要求的TypeScript 5分别使用各自锁文件。

HTTP来源为docs/contracts/openapi.yaml，事件来源为docs/contracts/events.schema.json。正式生成器为openapi-typescript 7.13.0和json-schema-to-typescript 16.0.0，事件运行时使用Ajv。接口字段或事件类型变化后，更新契约、生成文件并运行check:api、typecheck及tests/m3-contract-types.mjs。
