export interface paths {
    "/api/identity": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["readIdentity"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/identity/demo": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["issueDemoIdentity"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/organization": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["readOrganization"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** owner修改组织，禁用保留历史并禁止后续业务 */
        patch: operations["editOrganization"];
        trace?: never;
    };
    "/api/memberships": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["listMemberships"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/memberships/{id}/role": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch: operations["editMembershipRole"];
        trace?: never;
    };
    "/api/workspaces/{id}/members/{user_id}": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
                user_id: string;
            };
            cookie?: never;
        };
        get?: never;
        put: operations["editWorkspaceMembership"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/workspaces": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["listWorkspaces"];
        put?: never;
        post: operations["createWorkspace"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/workspaces/{id}": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        get: operations["readWorkspace"];
        put?: never;
        post?: never;
        delete: operations["disableWorkspace"];
        options?: never;
        head?: never;
        patch: operations["editWorkspace"];
        trace?: never;
    };
    "/api/agents": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["listAgents"];
        put?: never;
        post: operations["createAgent"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agents/{id}": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        get: operations["readAgent"];
        put?: never;
        post?: never;
        delete: operations["disableAgent"];
        options?: never;
        head?: never;
        patch: operations["editAgent"];
        trace?: never;
    };
    "/api/skills": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["listGovernanceSkills"];
        put?: never;
        post: operations["createGovernanceSkill"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/skills/{id}": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        get: operations["readGovernanceSkill"];
        put?: never;
        post?: never;
        delete: operations["archiveGovernanceSkill"];
        options?: never;
        head?: never;
        patch: operations["editGovernanceSkill"];
        trace?: never;
    };
    "/api/skills/{id}/versions": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        get: operations["listSkillVersions"];
        put?: never;
        post: operations["publishSkillVersion"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/skills/{id}/versions/{version_id}": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
                version_id: string;
            };
            cookie?: never;
        };
        get: operations["readSkillVersion"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/connectors": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["listConnectors"];
        put?: never;
        post: operations["createConnector"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/connectors/{id}": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        get: operations["readConnector"];
        put?: never;
        post?: never;
        delete: operations["disableConnector"];
        options?: never;
        head?: never;
        patch: operations["editConnector"];
        trace?: never;
    };
    "/api/connectors/{id}/validate": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["validateConnector"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/grants": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["listGrants"];
        put?: never;
        post: operations["createGrant"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/grants/{id}": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete: operations["revokeGrant"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agents/{id}/permission-rules": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        get: operations["listPermissionRules"];
        put?: never;
        post: operations["createPermissionRule"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agents/{id}/permission-rules/{rule_id}": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
                rule_id: string;
            };
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete: operations["revokePermissionRule"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/governance/stream": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["streamGovernanceEvents"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/audit": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["listAudit"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/audit/export": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["exportAudit"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/audit/verify": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["verifyAudit"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/diagnostics/backups": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["listDiagnosticBackups"];
        put?: never;
        post: operations["createDiagnosticBackup"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/diagnostics/restore": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["restoreDiagnosticBackup"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/health": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: {
            parameters: {
                query?: never;
                header?: never;
                path?: never;
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description ok */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            data?: {
                                status?: string;
                            };
                        };
                    };
                };
            };
        };
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/diagnostics": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 诊断入口（迁移失败/审计链断裂 → 只读诊断模式，backend-service §1） */
        get: {
            parameters: {
                query?: never;
                header?: never;
                path?: never;
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description 正常模式返回 mode=normal；诊断模式含原因与恢复提示 */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            data?: {
                                /** @enum {string} */
                                mode?: "normal" | "diagnostic";
                                reason?: string;
                                hint?: string;
                            };
                        };
                    };
                };
            };
        };
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/limits": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: {
            parameters: {
                query?: never;
                header?: never;
                path?: never;
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description 材料与导入限制 */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            data?: {
                                max_files?: number;
                                max_file_mb?: number;
                                max_folders?: number;
                            };
                        };
                    };
                };
            };
        };
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/conversations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 任务列表 */
        get: {
            parameters: {
                query?: never;
                header?: never;
                path?: never;
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description 任务列表 */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            data?: components["schemas"]["ConversationSummary"][];
                        };
                    };
                };
            };
        };
        put?: never;
        /** 发送首条指令并创建任务（F001） */
        post: {
            parameters: {
                query?: never;
                header?: never;
                path?: never;
                cookie?: never;
            };
            requestBody: {
                content: {
                    "application/json": components["schemas"]["ConversationCreateRequest"];
                };
            };
            responses: {
                /** @description 创建成功 */
                201: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            data?: components["schemas"]["ConversationCreateData"];
                        };
                    };
                };
                /** @description 指令非法或全部材料被拒 */
                422: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": components["schemas"]["Error"];
                    };
                };
            };
        };
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/conversations/{id}": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** 删除任务 */
        delete: {
            parameters: {
                query?: never;
                header?: never;
                path: {
                    id: string;
                };
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description 已删除（无信封） */
                204: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content?: never;
                };
                404: components["responses"]["ErrEnvelope"];
            };
        };
        options?: never;
        head?: never;
        /** 重命名（保留原始指令与审计） */
        patch: {
            parameters: {
                query?: never;
                header?: never;
                path: {
                    id: string;
                };
                cookie?: never;
            };
            requestBody: {
                content: {
                    "application/json": components["schemas"]["ConversationPatchRequest"];
                };
            };
            responses: {
                /** @description 已更新 */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            data?: components["schemas"]["ConversationSummary"];
                        };
                    };
                };
                404: components["responses"]["ErrEnvelope"];
            };
        };
        trace?: never;
    };
    "/api/conversations/{id}/scope": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: {
            parameters: {
                query?: never;
                header?: never;
                path: {
                    id: string;
                };
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description 工作空间/资料目录/文件夹范围 */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            data?: components["schemas"]["Scope"];
                        };
                    };
                };
            };
        };
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/conversations/{id}/instructions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 后续指令（can_send 直跑 / can_queue 入队 / 等待审批 409） */
        post: {
            parameters: {
                query?: never;
                header?: never;
                path: {
                    id: string;
                };
                cookie?: never;
            };
            requestBody: {
                content: {
                    "application/json": components["schemas"]["InstructionRequest"];
                };
            };
            responses: {
                /** @description 已受理 */
                202: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            data?: {
                                /**
                                 * @description cancelled = 该 client_request_id 对应指令已被用户取消，重试不再入队（外审回稿）
                                 * @enum {string}
                                 */
                                mode?: "started" | "queued" | "cancelled";
                                queue_position?: number | null;
                                /** @description started 时为直发新建的任务；C7 落地补充 */
                                task_run_id?: string | null;
                            };
                        };
                    };
                };
                409: components["responses"]["ErrEnvelope"];
            };
        };
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/conversations/{id}/state": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: {
            parameters: {
                query?: never;
                header?: never;
                path: {
                    id: string;
                };
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description FSM 快照（含 at_global_seq 与队列明细） */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            data?: components["schemas"]["ConversationState"];
                        };
                    };
                };
            };
        };
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/conversations/{id}/messages": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 聊天记录（默认最新 50 条，before=同会话消息 id 排他游标）
         * @description 每页按 created_at、id 正序返回。before 读取该消息之前的最新一页；继续读取更早记录时使用 items 第一条的 id。会话或游标不存在、游标属于其他会话时返回 404。
         */
        get: {
            parameters: {
                query?: {
                    limit?: number;
                    /** @description 返回该消息 id 之前的记录，不包含游标消息 */
                    before?: string;
                };
                header?: never;
                path: {
                    id: string;
                };
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description 分页消息 */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            data?: {
                                items?: components["schemas"]["Message"][];
                                /** @description 是否还有更早的消息 */
                                has_more?: boolean;
                            };
                        };
                    };
                };
                401: components["responses"]["ErrEnvelope"];
                404: components["responses"]["ErrEnvelope"];
                422: components["responses"]["ErrEnvelope"];
            };
        };
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/conversations/{id}/task-runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: {
            parameters: {
                query?: never;
                header?: never;
                path: {
                    id: string;
                };
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description 执行记录列表 */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            data?: components["schemas"]["TaskRunSummary"][];
                        };
                    };
                };
            };
        };
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/conversations/{id}/stream": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 会话级 SSE（from=排他游标；控制帧 resync/shutdown/ping 见文件头注释） */
        get: {
            parameters: {
                query?: {
                    from?: number;
                };
                header?: never;
                path: {
                    id: string;
                };
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description text/event-stream（无信封） */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content?: never;
                };
                503: components["responses"]["ErrEnvelope"];
            };
        };
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/conversations/{id}/queue/continue": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: {
            parameters: {
                query?: never;
                header?: never;
                path: {
                    id: string;
                };
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description 队首已启动（202 无响应体，属有意设计） */
                202: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content?: never;
                };
                409: components["responses"]["ErrEnvelope"];
            };
        };
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/conversations/{id}/queue/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: {
            parameters: {
                query?: never;
                header?: never;
                path: {
                    id: string;
                };
                cookie?: never;
            };
            requestBody: {
                content: {
                    "application/json": components["schemas"]["QueueCancelRequest"];
                };
            };
            responses: {
                /** @description 已取消（发送记录保留） */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            data?: {
                                cancelled_ids?: string[];
                            };
                        };
                    };
                };
            };
        };
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/conversations/{id}/artifacts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: {
            parameters: {
                query?: {
                    task_run_id?: string;
                };
                header?: never;
                path: {
                    id: string;
                };
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description 产物列表（missing 由探测更新） */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            data?: components["schemas"]["Artifact"][];
                        };
                    };
                };
            };
        };
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/conversations/{id}/pending-verifications": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: {
            parameters: {
                query?: never;
                header?: never;
                path: {
                    id: string;
                };
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description 待核验清单 */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            data?: components["schemas"]["PendingVerification"][];
                        };
                    };
                };
            };
        };
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/conversations/{id}/questions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 挂起中的提问（刷新后恢复提问卡） */
        get: {
            parameters: {
                query?: never;
                header?: never;
                path: {
                    id: string;
                };
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description 待回答提问列表 */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            data?: components["schemas"]["QuestionPending"][];
                        };
                    };
                };
            };
        };
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/questions/{requestId}/answer": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 提交提问回答（answer=null 表示用户取消，按"拒绝回答"告知模型） */
        post: {
            parameters: {
                query?: never;
                header?: never;
                path: {
                    requestId: string;
                };
                cookie?: never;
            };
            requestBody: {
                content: {
                    "application/json": components["schemas"]["QuestionAnswerRequest"];
                };
            };
            responses: {
                /** @description 已记录（question.answered 事件） */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            data?: {
                                request_id?: string;
                                /** Format: date-time */
                                answered_at?: string;
                            };
                        };
                    };
                };
                409: components["responses"]["ErrEnvelope"];
            };
        };
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/task-runs/{id}/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** SSE 流（from=任务内排他 seq）；带 limit 时为 JSON 分页（after_seq 排他，limit ≤500） */
        get: operations["readRunEvents"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/task-runs/{id}/attempts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: {
            parameters: {
                query?: never;
                header?: never;
                path: {
                    id: string;
                };
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description 尝试列表（恢复边界与配置指纹可见） */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            data?: {
                                attempt_no?: number;
                                /** @enum {string} */
                                kind?: "initial" | "resume";
                                outcome?: string | null;
                                context_fingerprint?: {
                                    [key: string]: unknown;
                                };
                                resume_reason?: string | null;
                                /** Format: date-time */
                                started_at?: string;
                                /** Format: date-time */
                                ended_at?: string | null;
                            }[];
                        };
                    };
                };
            };
        };
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/task-runs/{id}/approvals": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: {
            parameters: {
                query?: {
                    status?: "pending" | "resolved";
                };
                header?: never;
                path: {
                    id: string;
                };
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description 审批列表 */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            data?: components["schemas"]["Approval"][];
                        };
                    };
                };
            };
        };
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/task-runs/{id}/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: {
            parameters: {
                query?: never;
                header?: never;
                path: {
                    id: string;
                };
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description 取消中（202 无响应体；终态时序见 harness-design §3） */
                202: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content?: never;
                };
            };
        };
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/task-runs/{id}/resume": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: {
            parameters: {
                query?: never;
                header?: never;
                path: {
                    id: string;
                };
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description 恢复中（新 attempt，绑定新配置版本；data.warnings 为重建期降级清单） */
                202: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            data?: {
                                warnings?: string[];
                            };
                        };
                    };
                };
                409: components["responses"]["ErrEnvelope"];
            };
        };
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/tool-approvals/{callId}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 审批决定——同决定重试幂等返回首次结果；不同决定 → 409 APPROVAL_STALE */
        post: {
            parameters: {
                query?: never;
                header?: never;
                path: {
                    callId: string;
                };
                cookie?: never;
            };
            requestBody: {
                content: {
                    "application/json": components["schemas"]["ApprovalDecisionRequest"];
                };
            };
            responses: {
                /** @description 已决定（data 含首次决定与时间） */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            data?: {
                                decision?: string;
                                /** Format: date-time */
                                decided_at?: string;
                                idempotent_replay?: boolean;
                            };
                        };
                    };
                };
                409: components["responses"]["ErrEnvelope"];
            };
        };
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/tool-calls/{callId}/verification": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: {
            parameters: {
                query?: never;
                header?: never;
                path: {
                    callId: string;
                };
                cookie?: never;
            };
            requestBody: {
                content: {
                    "application/json": components["schemas"]["VerificationRequest"];
                };
            };
            responses: {
                /** @description 已记录（tool.verification_submitted 事件 + 审计链） */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            data?: {
                                call_id?: string;
                                /** @enum {string} */
                                status?: "completed" | "not_executed";
                                verdict?: string;
                            };
                        };
                    };
                };
                409: components["responses"]["ErrEnvelope"];
            };
        };
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/settings": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: {
            parameters: {
                query?: never;
                header?: never;
                path?: never;
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description 后端配置（密钥脱敏） */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            data?: components["schemas"]["Settings"];
                        };
                    };
                };
            };
        };
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** 更新配置——仅影响后续执行的配置版本；运行中执行保持绑定版 */
        patch: {
            parameters: {
                query?: never;
                header?: never;
                path?: never;
                cookie?: never;
            };
            requestBody: {
                content: {
                    "application/json": components["schemas"]["SettingsPatchRequest"];
                };
            };
            responses: {
                /** @description 已更新 */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            data?: components["schemas"]["SettingsPatchData"];
                        };
                    };
                };
                500: components["responses"]["ErrEnvelope"];
            };
        };
        trace?: never;
    };
    "/api/memory/stores/{store_type}/{store_id}": {
        parameters: {
            query: {
                workspace_id: components["parameters"]["MemoryWorkspace"];
                agent_id: components["parameters"]["MemoryAgent"];
            };
            header?: never;
            path: {
                store_type: components["parameters"]["MemoryStoreType"];
                store_id: components["parameters"]["MemoryStoreId"];
            };
            cookie?: never;
        };
        /** 读取库原文、配额及条目；user 使用 owner 标识，范围由现有身份核对 */
        get: operations["readMemoryStore"];
        put?: never;
        /** 新建条目；服务生成身份、来源和高风险审核状态 */
        post: operations["createMemoryEntry"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/memory/stores/{store_type}/{store_id}/entries/{entry_hash}": {
        parameters: {
            query: {
                workspace_id: components["parameters"]["MemoryWorkspace"];
                agent_id: components["parameters"]["MemoryAgent"];
            };
            header?: never;
            path: {
                store_type: components["parameters"]["MemoryStoreType"];
                store_id: components["parameters"]["MemoryStoreId"];
                entry_hash: string;
            };
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** 归档式删除，保留正文、metadata 和恢复位置 */
        delete: operations["archiveMemoryEntry"];
        options?: never;
        head?: never;
        /** 编辑正文，首行变化保留 entry_id 并更新哈希；陈旧修订409 */
        patch: operations["editMemoryEntry"];
        trace?: never;
    };
    "/api/memory/stores/{store_type}/{store_id}/entries/{entry_hash}/{action}": {
        parameters: {
            query: {
                workspace_id: components["parameters"]["MemoryWorkspace"];
                agent_id: components["parameters"]["MemoryAgent"];
            };
            header?: never;
            path: {
                store_type: components["parameters"]["MemoryStoreType"];
                store_id: components["parameters"]["MemoryStoreId"];
                entry_hash: string;
                action: "pin" | "unpin" | "archive" | "restore";
            };
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 固定、取消固定、归档或恢复条目，追加账本并校验配额 */
        post: operations["changeMemoryEntryState"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/memory/stores/{store_type}/{store_id}/entries/{entry_hash}/review": {
        parameters: {
            query: {
                workspace_id: components["parameters"]["MemoryWorkspace"];
                agent_id: components["parameters"]["MemoryAgent"];
            };
            header?: never;
            path: {
                store_type: components["parameters"]["MemoryStoreType"];
                store_id: components["parameters"]["MemoryStoreId"];
                entry_hash: string;
            };
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 人工批准或拒绝高风险事实；只有批准解除 needs_review，旧会话快照保持不变 */
        post: operations["reviewMemoryEntry"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/memory/ledger": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 完整前后状态按 id 降序分页，after 绑定同库范围且排他 */
        get: operations["listMemoryLedger"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/memory/ledger/{id}/rollback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 恢复指定账本的 before 正文、metadata 和文件，追加新账本 */
        post: operations["restoreMemoryLedger"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/memory/search": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 当前身份范围内的历史和记忆检索，禁止返回未审核事实及指令性原文 */
        get: operations["searchMemory"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/memory/skills": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 当前工作区及员工的真实 Skill 索引，展示不增加命中统计 */
        get: operations["listMemorySkills"];
        put?: never;
        /** 服务分配 Skill 标识，校验真实工作区、员工和名称唯一性 */
        post: operations["createMemorySkill"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/memory/skills/{id}/file": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 读取支撑文件，拒绝路径穿越、目录外符号链接及未授权 Skill */
        get: operations["readMemorySkillFile"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/memory/jobs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["listMemoryJobs"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/memory/jobs/{id}": {
        parameters: {
            query?: {
                /** @description 可选当前范围，必须与 agent_id 同时提供并匹配作业 */
                workspace_id?: string;
                agent_id?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["readMemoryJob"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/memory/jobs/{id}/cancel": {
        parameters: {
            query?: {
                /** @description 可选当前范围，必须与 agent_id 同时提供并匹配作业 */
                workspace_id?: string;
                agent_id?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 两秒内取消模型流与等待审批；重复取消返回已保存终态 */
        post: operations["cancelMemoryJob"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/memory/jobs/{id}/approvals/{approval_id}": {
        parameters: {
            query?: {
                /** @description 可选当前范围，必须与 agent_id 同时提供并匹配作业 */
                workspace_id?: string;
                agent_id?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 审批绑定独立作业、调用和参数；相同决定幂等，取消后的旧审批409 */
        post: operations["decideMemoryJobApproval"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/memory/curate/run": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 空闲时受理确定性治理；重复 client_request_id 返回同一真实作业 */
        post: operations["startMemoryCurator"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/memory/stream": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 当前身份可读范围的记忆及作业事件，先注册再补历史，from 为排他全局水位 */
        get: operations["streamMemoryEvents"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cron/jobs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["listCronJobs"];
        put?: never;
        post: operations["createCronJob"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cron/jobs/{id}": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        get: operations["readCronJob"];
        put?: never;
        post?: never;
        delete: operations["deleteCronJob"];
        options?: never;
        head?: never;
        patch: operations["editCronJob"];
        trace?: never;
    };
    "/api/cron/jobs/{id}/run-now": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["runCronJobNow"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cron/jobs/{id}/runs": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        get: operations["listCronOccurrences"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cron/authorization-preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["previewCronAuthorization"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cron/proposals/{id}": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        get: operations["readScheduleProposal"];
        put?: never;
        post: operations["decideScheduleProposal"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cron/stream": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["streamCronEvents"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/task-runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["listRunCenterRuns"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/metrics": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["readRunMetrics"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/task-runs/{id}": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        get: operations["readRunCenterRun"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/task-runs/{id}/events/{seq}": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
                seq: number;
            };
            cookie?: never;
        };
        get: operations["locateRunEvent"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/task-runs/{id}/calls/{call_id}": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
                call_id: string;
            };
            cookie?: never;
        };
        get: operations["readRunCall"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/task-runs/{id}/audit-report": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        get: operations["readTraceReport"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/task-runs/{id}/review": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["requestTraceReview"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/reviews/jobs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["listTraceJobs"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/reviews/jobs/{id}": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        get: operations["readTraceJob"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/reviews/jobs/{id}/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["cancelTraceJob"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/audit-reports/{id}/promote-skill": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["promoteTraceSkill"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/notifications": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["listNotifications"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/notifications/{id}": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch: operations["acknowledgeNotification"];
        trace?: never;
    };
    "/api/runtime/exit-impact": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["readExitImpact"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runtime/power-event": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["recordPowerEvent"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/settings/desktop": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["readDesktopPreferences"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch: operations["editDesktopPreferences"];
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        Error: {
            error: {
                code: string;
                message: string;
                detail?: {
                    [key: string]: unknown;
                };
            };
        };
        ConversationSummary: {
            id?: string;
            title?: string | null;
            /** @enum {string} */
            status?: "active" | "archived";
            agent_name?: string;
            /** Format: date-time */
            last_activity_at?: string;
            /** @enum {string} */
            state_badge?: "idle" | "running" | "waiting_approval" | "waiting_question" | "interrupted" | "failed";
        };
        TaskRunSummary: {
            id?: string;
            instruction?: string;
            /** @enum {string} */
            status?: "queued" | "running" | "waiting_user" | "waiting_verification" | "interrupted" | "completed" | "failed" | "cancelled";
            current_attempt_no?: number;
            cron_job_id?: string | null;
            /** Format: date-time */
            created_at?: string;
            /** Format: date-time */
            finished_at?: string | null;
        };
        ConversationState: {
            /** @enum {string} */
            state?: "idle" | "starting" | "running" | "error";
            waiting_approvals?: number;
            waiting_questions?: number;
            queue_paused?: boolean;
            /** @description 快照反映到的全局事件位置；≤它的 global_seq 已被吸收，续播跳过（v0.3） */
            at_global_seq?: number;
            queue?: {
                id?: string;
                text?: string;
                /** Format: date-time */
                enqueued_at?: string;
            }[];
            can_send?: boolean;
            can_queue?: boolean;
            can_cancel?: boolean;
            can_continue_queue?: boolean;
            current_task_run_id?: string | null;
        };
        Scope: {
            workspace_dir?: string;
            materials_dir?: string;
            folders?: {
                path?: string;
                /**
                 * @description v1.7 起授权界面读写分列
                 * @enum {string}
                 */
                access?: "read" | "read_write";
            }[];
        };
        MaterialImportResult: {
            original_path?: string;
            stored_name?: string;
            size_bytes?: number;
            error?: string | null;
        };
        ConversationCreateRequest: {
            /**
             * @description 真实登记且当前身份可访问的工作区
             * @default default
             */
            workspace_id: string;
            agent_id?: string;
            instruction: string;
            /** @description 幂等键——网络重试不产生重复任务（v0.3） */
            client_request_id?: string;
            import_files?: string[];
            folders?: string[];
        };
        ConversationCreateData: {
            conversation?: components["schemas"]["ConversationSummary"];
            materials?: components["schemas"]["MaterialImportResult"][];
            /** @description 授权文件夹逐项结果（不存在/超限逐项报 error；C7 落地补充） */
            folders?: {
                path?: string;
                error?: string | null;
            }[];
            scope?: components["schemas"]["Scope"];
            task_run_id?: string;
        };
        ConversationPatchRequest: {
            title?: string;
        };
        InstructionRequest: {
            text: string;
            client_request_id?: string;
        };
        Message: {
            id?: string;
            /** @enum {string} */
            role?: "user" | "assistant";
            content?: string;
            task_run_id?: string | null;
            /** Format: date-time */
            created_at?: string;
        };
        QueueCancelRequest: {
            item_ids?: string[];
            all?: boolean;
        };
        Approval: {
            call_id?: string;
            tool?: string;
            target?: string;
            input_hash?: string;
            /** @enum {string} */
            risk?: "low" | "medium" | "high";
            always_scope_preview?: string | null;
            /** Format: date-time */
            requested_at?: string;
        };
        ApprovalDecisionRequest: {
            /** @enum {string} */
            decision: "allow_once" | "allow_always" | "reject_once" | "reject_always";
            /** @description 可选——与审批卡绑定的参数哈希，不一致 409 APPROVAL_STALE（v0.3.1 补） */
            input_hash?: string;
        };
        QuestionPending: {
            request_id?: string;
            question?: string;
            options?: string[];
            /** Format: date-time */
            asked_at?: string;
        };
        QuestionAnswerRequest: {
            /** @description 用户的回答；null = 用户取消回答（按"拒绝回答"告知模型） */
            answer?: string | null;
        };
        PendingVerification: {
            call_id?: string;
            tool?: string;
            input?: {
                [key: string]: unknown;
            };
            /** Format: date-time */
            dispatched_at?: string;
            evidence?: string;
        };
        VerificationRequest: {
            /** @enum {string} */
            verdict: "confirmed_executed" | "confirmed_not_executed";
            note?: string;
        };
        Artifact: {
            id?: string;
            task_run_id?: string;
            tool_call_id?: string;
            name?: string;
            path?: string;
            ext?: string;
            size_bytes?: number | null;
            /** @enum {string} */
            status?: "generating" | "ready" | "failed" | "missing";
            /** Format: date-time */
            created_at?: string;
        };
        Settings: {
            config_version?: string;
            memory?: components["schemas"]["MemorySettings"];
            models?: {
                main?: components["schemas"]["ModelSlot"];
                aux?: components["schemas"]["ModelSlot"];
            };
            gates?: {
                max_steps?: number;
                stall_seconds?: number;
                repeat_limit?: number;
                global_concurrency?: number;
                /** @description 任务累计 token 硬上限（超限 RUN_FAILED(token_budget)；压缩是 M1）——C8 */
                token_budget?: number;
            };
            limits?: {
                max_files?: number;
                max_file_mb?: number;
                max_folders?: number;
            };
        };
        ModelSlot: {
            /**
             * @description glm 使用 Anthropic Messages；openai-compatible 使用 Chat Completions
             * @enum {string}
             */
            provider?: "glm" | "openai-compatible";
            model?: string;
            context_window?: number | null;
            context_window_source?: string | null;
            tokenizer_repository?: string | null;
            tokenizer_revision?: string | null;
            tokenizer_sha256?: string | null;
            prompt_format?: string | null;
            max_tokens?: number;
            api_key_configured?: boolean;
            /** @description 尾 4 位 */
            api_key_hint?: string;
            /**
             * @description v1.1：该槽当前生效来源
             * @enum {string}
             */
            effective_source?: "env" | "file";
        };
        SettingsPatchRequest: {
            models?: {
                main?: {
                    [key: string]: unknown;
                };
                aux?: {
                    [key: string]: unknown;
                };
            };
            /** @description 显式清除某槽密钥 */
            api_key_clear?: ("main" | "aux")[];
            gates?: {
                [key: string]: unknown;
            };
            limits?: {
                [key: string]: unknown;
            };
            memory?: components["schemas"]["MemorySettings"];
        };
        SettingsPatchData: {
            settings?: components["schemas"]["Settings"];
            /** @description 被环境变量覆盖而未生效的字段 */
            ignored_fields?: string[];
            /** @description 需重启生效的本次触碰字段（gates.global_concurrency 在进程启动期构造信号量，PATCH 仅落盘——C8 二轮回稿） */
            restart_required?: string[];
            /** @description 审计入链状态（外审回稿：一切配置变更入审计链） */
            audit?: {
                /**
                 * @description pending = 审计写失败已落待办文件待补链；failed = 待办也无法落盘
                 * @enum {string}
                 */
                status?: "ok" | "pending" | "failed";
            };
        };
        MemorySettings: {
            /** @default 1400 */
            user_quota: number;
            /** @default 2200 */
            workspace_quota: number;
            /** @default 2700 */
            soul_quota: number;
            /** @default false */
            write_approval: boolean;
        };
        MemorySource: {
            /** @enum {string} */
            actor_type: "user" | "agent" | "curator" | "system";
            actor_id: string;
            conversation_id?: string | null;
            task_run_id?: string | null;
            job_id?: string | null;
            manual_edit_id?: string | null;
        };
        MemoryEntry: {
            entry_id: string;
            entry_hash: string;
            text: string;
            /** @enum {string} */
            state: "active" | "stale" | "archived" | "pinned";
            hits: number;
            /** Format: date-time */
            last_hit_at: string | null;
            /** Format: date-time */
            created_at: string;
            source: components["schemas"]["MemorySource"];
            basis: string;
            needs_review: boolean;
            reviewed_by?: string | null;
            /** Format: date-time */
            reviewed_at?: string | null;
        };
        MemoryStore: {
            /** @enum {string} */
            store_type: "user" | "workspace" | "soul" | "skill";
            store_id: string;
            workspace_id?: string | null;
            agent_id?: string | null;
            revision: number;
            text: string;
            sha256: string;
            metadata_sha256: string;
            used_characters: number;
            quota: number | null;
            entries: components["schemas"]["MemoryEntry"][];
            at_global_seq: number;
            files?: string[];
            description?: string;
            name?: string;
        };
        MemoryChangeRequest: {
            change_id: string;
            expected_revision: number;
            text?: string;
            basis: string;
            conversation_id?: string | null;
            description?: string;
            name?: string;
            /** @description Skill 相对路径到正文，null 删除；服务校验目录边界 */
            files?: {
                [key: string]: string | null;
            };
            /** @enum {string} */
            review_decision?: "approve" | "reject";
        };
        MemoryChange: {
            change_id: string;
            ledger_id: number;
            revision: number;
            global_seq: number;
            entry_id?: string | null;
            entry_hash?: string | null;
            idempotent_replay: boolean;
            /** @description Skill发布对应的不可变版本 */
            version_id?: string;
            version_no?: number;
        };
        MemoryWriteRequest: components["schemas"]["MemoryChangeRequest"] & unknown;
        MemoryReviewRequest: components["schemas"]["MemoryChangeRequest"] & unknown;
        MemoryLedger: {
            id: number;
            change_id: string;
            /** @enum {string} */
            store_type: "user" | "workspace" | "soul" | "skill";
            store_id: string;
            /** @enum {string} */
            action: "create" | "update" | "archive" | "restore" | "pin" | "unpin" | "review" | "rollback" | "batch" | "stale";
            before_text: string | null;
            after_text: string | null;
            before_metadata: {
                [key: string]: unknown;
            };
            after_metadata: {
                [key: string]: unknown;
            };
            before_files?: components["schemas"]["MemoryLedgerFile"][];
            after_files?: components["schemas"]["MemoryLedgerFile"][];
            restored_ledger_id?: number | null;
            source: components["schemas"]["MemorySource"];
            /** Format: date-time */
            created_at: string;
        };
        MemoryLedgerFile: {
            path: string;
            content: string | null;
            sha256: string | null;
        };
        MemoryJob: {
            id: string;
            /** @enum {string} */
            kind: "summary" | "memory_review" | "skill_review" | "curate";
            /** @enum {string} */
            status: "queued" | "running" | "waiting_approval" | "completed" | "cancelled" | "interrupted" | "failed";
            conversation_id: string | null;
            task_run_id: string | null;
            trigger_global_seq: number;
            model: string | null;
            config_version: string;
            usage: {
                input_tokens: number;
                output_tokens: number;
            };
            error: string | null;
            report?: {
                [key: string]: unknown;
            } | null;
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            finished_at: string | null;
            approvals: {
                id: string;
                job_id: string;
                call_id: string;
                input_hash: string;
                /** @enum {string} */
                tool: "memory_write" | "skill_patch";
                input: {
                    [key: string]: unknown;
                };
                /** @enum {string} */
                status: "pending" | "allowed" | "rejected" | "expired";
            }[];
            calls?: {
                ordinal: number;
                type: string;
                payload: {
                    [key: string]: unknown;
                };
                /** Format: date-time */
                created_at: string;
            }[];
        };
        MemorySearchHit: {
            id: string;
            /** @enum {string} */
            kind: "message" | "memory" | "summary";
            text: string;
            source: {
                [key: string]: unknown;
            };
            /** Format: date-time */
            created_at: string;
            store_type?: string | null;
            store_id?: string | null;
            /** @enum {string|null} */
            state?: "active" | "stale" | "archived" | "pinned" | null;
        };
        SkillIndexItem: {
            id: string;
            workspace_id: string;
            agent_id: string;
            name: string;
            description: string;
            revision: number;
            /** @description 当前任务绑定版本 */
            version_id?: string;
            /** @enum {string} */
            state: "active" | "stale" | "archived" | "pinned";
            hits: number;
            /** Format: date-time */
            last_hit_at: string | null;
        };
        SkillCreateRequest: {
            change_id: string;
            /** @constant */
            expected_revision: 0;
            workspace_id: string;
            agent_id: string;
            name: string;
            description: string;
            text: string;
            basis: string;
            conversation_id?: string | null;
            files?: {
                [key: string]: string;
            };
        };
        GovernanceChangeRequest: {
            change_id: string;
            expected_revision: number;
        };
        Identity: {
            credential_owner_id: string;
            effective_user_id: string;
            name: string;
            org_id: string;
            /** @enum {string} */
            role: "owner" | "admin" | "member";
            demo: boolean;
            workspace_ids: string[];
            /** @enum {string} */
            organization_status?: "active" | "disabled" | "archived";
        };
        DemoIdentityRequest: {
            user_id: string;
            change_id: string;
        };
        GovernanceResource: {
            id: string;
            name: string;
            org_id?: string;
            workspace_id?: string;
            /** @description 服务分配的工作区目录 */
            data_dir?: string;
            /** @enum {string} */
            status: "active" | "disabled" | "archived";
            revision: number;
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            updated_at?: string;
            spec?: components["schemas"]["AgentSpec"];
            description?: string;
            /** @enum {string} */
            source?: "user" | "agent" | "system";
            current_version_id?: string | null;
            /** @enum {string} */
            type?: "http" | "mcp";
            config?: components["schemas"]["ConnectorConfig"];
            credential_configured?: boolean;
        };
        WorkspaceRequest: components["schemas"]["GovernanceChangeRequest"] & {
            name: string;
            /** @enum {string} */
            status?: "active" | "disabled" | "archived";
        };
        AgentSpec: {
            position: string;
            /** @enum {string} */
            model_slot: "main" | "aux";
            skill_ids: string[];
            connector_ids: string[];
        };
        AgentRequest: components["schemas"]["GovernanceChangeRequest"] & {
            workspace_id: string;
            name: string;
            /** @enum {string} */
            status?: "active" | "disabled" | "archived";
            spec: components["schemas"]["AgentSpec"];
        };
        GovernanceResourcePatch: components["schemas"]["GovernanceChangeRequest"] & {
            name?: string;
            /** @enum {string} */
            status?: "active" | "disabled" | "archived";
            spec?: components["schemas"]["AgentSpec"];
            description?: string;
            config?: components["schemas"]["ConnectorConfig"];
            /** @description 服务端绑定认证信息；null清除，缺省不变 */
            credential?: string | null;
        };
        Membership: {
            id: string;
            org_id: string;
            user_id: string;
            name: string;
            /** @enum {string} */
            role: "owner" | "admin" | "member";
            /** @enum {string} */
            status: "active" | "disabled";
            revision: number;
            workspace_ids: string[];
            workspace_access?: {
                workspace_id: string;
                enabled: boolean;
                revision: number;
            }[];
        };
        MembershipRoleRequest: components["schemas"]["GovernanceChangeRequest"] & {
            /** @enum {string} */
            role: "owner" | "admin" | "member";
            /** @enum {string} */
            status?: "active" | "disabled";
        };
        WorkspaceMemberRequest: components["schemas"]["GovernanceChangeRequest"] & {
            enabled: boolean;
        };
        GovernanceSkillCreate: components["schemas"]["SkillCreateRequest"];
        SkillVersionRequest: components["schemas"]["MemoryChangeRequest"] & {
            /** @description 恢复该版本正文与支撑文件，追加新版本；不能与text/files/name/description同时提供 */
            restore_version_id?: string;
        };
        SkillVersion: {
            id: string;
            skill_id: string;
            version_no: number;
            change_id: string;
            ledger_id: number;
            content: string;
            metadata: {
                [key: string]: unknown;
            };
            files: components["schemas"]["MemoryLedgerFile"][];
            sha256: string;
            created_by: string;
            /** Format: date-time */
            created_at: string;
        };
        ConnectorToolPolicy: {
            read_only: boolean;
            destructive: boolean;
            /** @enum {string} */
            risk_level: "low" | "medium" | "high";
            needs_approval: boolean;
            /** @default 60000 */
            timeout_ms: number;
        };
        ConnectorConfig: {
            /** Format: uri */
            url?: string;
            allowed_hosts: string[];
            allowed_ports: number[];
            /** @default false */
            allow_loopback: boolean;
            /** @enum {string} */
            transport?: "stdio" | "streamable_http";
            /** @description stdio 固定管理员批准的可执行路径 */
            command?: string;
            args?: string[];
            /** @description stdio明确登记的绝对代码文件；规范化目标与SHA绑定配置修订，变更需要提交新修订 */
            startup_files?: string[];
            /** @default Authorization */
            credential_header: string;
            tool_policies?: {
                [key: string]: components["schemas"]["ConnectorToolPolicy"];
            };
            idempotent_endpoints?: {
                /** @enum {string} */
                method: "POST" | "PUT" | "PATCH" | "DELETE";
                path: string;
                /** @description 上游真实去重承诺及验证依据 */
                guarantee: string;
            }[];
        };
        ConnectorRequest: components["schemas"]["GovernanceChangeRequest"] & {
            workspace_id: string;
            name: string;
            /** @enum {string} */
            type: "http" | "mcp";
            config: components["schemas"]["ConnectorConfig"];
            credential?: string;
        };
        GrantRequest: {
            change_id: string;
            /** @enum {string} */
            resource_type: "skill" | "connector" | "agent";
            resource_id: string;
            /** @enum {string} */
            grantee_type: "agent" | "user";
            grantee_id: string;
        };
        Grant: {
            id: string;
            change_id: string;
            /** @enum {string} */
            resource_type: "skill" | "connector" | "agent";
            resource_id: string;
            /** @enum {string} */
            grantee_type: "agent" | "user";
            grantee_id: string;
            workspace_id: string;
            revision: number;
            granted_by_user_id: string;
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            revoked_at: string | null;
        };
        PermissionRuleRequest: {
            change_id: string;
            tool_name: string;
            /** @description 路径为规范化目录；bash为完整命令；HTTP为连接器约束内域名 */
            pattern: string;
            /** @enum {string} */
            effect: "allow" | "deny";
        };
        PermissionRule: {
            id: string;
            agent_id: string;
            tool_name: string;
            pattern: string;
            /** @enum {string} */
            effect: "allow" | "deny";
            revision: number;
            created_by_user_id: string;
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            revoked_at: string | null;
            /** @description 导入的历史规则允许没有change_id */
            change_id: string | null;
        };
        AuditRecord: {
            seq: number;
            /** Format: date-time */
            ts: string;
            /** @enum {string} */
            actor_type: "user" | "agent" | "system" | "curator";
            actor_id: string;
            action: string;
            resource_type: string | null;
            resource_id: string | null;
            /** @description 已脱敏，包含真实及有效身份和范围 */
            detail: {
                [key: string]: unknown;
            };
            prev_hash: string;
            hash: string;
        };
        AuditVerification: {
            ok: boolean;
            internal: {
                ok: boolean;
                checked_rows: number;
                broken_at: number | null;
                reason: string | null;
            };
            anchor: {
                ok: boolean;
                /** @enum {string} */
                status: "verified" | "empty" | "missing" | "malformed" | "mismatch";
                seq: number | null;
                hash: string | null;
            };
        };
        DiagnosticBackup: {
            id: string;
            /** @enum {string} */
            kind: "database" | "directory";
            /** Format: date-time */
            created_at: string;
            verified: boolean;
            database_sha256: string;
            anchor_seq: number;
            anchor_hash: string;
        };
        RunEvent: {
            global_seq: number;
            task_run_id?: string;
            seq?: number;
            attempt_no?: number | null;
            conversation_id?: string;
            type: string;
            /** @description 展示投影移除凭据与隐藏推理；完整事实仍保存在服务端 */
            payload: {
                [key: string]: unknown;
            };
            /** Format: date-time */
            ts: string;
        };
        RunRecord: components["schemas"]["TaskRunSummary"] & {
            conversation_id: string;
            workspace_id: string;
            agent_id: string;
            owner_id: string;
            /** @enum {string} */
            source: "user" | "cron" | "manual";
            occurrence_id?: string | null;
            agent_spec_snapshot?: {
                [key: string]: unknown;
            };
            skill_versions?: {
                [key: string]: string;
            };
            error?: string | null;
            at_global_seq: number;
        };
        RunMetrics: {
            task_count: number;
            completed_count: number;
            /** @description completed_count/task_count；空集合为null，任务完成率不证明产物正确性 */
            completion_rate: number | null;
            status_counts: {
                [key: string]: number;
            };
            step_count: number;
            llm_call_count: number;
            tool_call_count: number;
            tool_status_counts: {
                [key: string]: number;
            };
            /** @description 任一相关调用usage缺失时为null，已知部分单独保留 */
            prompt_tokens: number | null;
            completion_tokens: number | null;
            known_prompt_tokens?: number;
            known_completion_tokens?: number;
            usage_complete: boolean;
            missing_usage_calls: number;
            latency_ms: number | null;
            at_global_seq: number;
        };
        RunCall: {
            id: string;
            /** @enum {string} */
            kind: "model" | "tool";
            task_run_id: string;
            attempt_no: number | null;
            seq: number;
            step_id?: string | null;
            status: string;
            summary: {
                [key: string]: unknown;
            };
            events?: components["schemas"]["RunEvent"][];
            /** @description 分别指出缺失的投影、事件或工件 */
            missing?: string[];
        };
        CronSchedule: {
            /** @constant */
            kind: "at";
            /** @description UTC Unix毫秒；过期时登记missed并结束一次性计划 */
            at_ms: number;
            /** @description IANA时区 */
            tz: string;
        } | {
            /** @constant */
            kind: "every";
            /** @description 下一次时间仍须位于支持的日期范围，越界返回422 */
            every_ms: number;
            tz: string;
        } | {
            /** @constant */
            kind: "cron";
            /** @description croniter五字段标准表达式，或第六字段为秒；拒绝宏、年度字段及H/R非确定性表达式，分钟/小时/日期/月份/星期按库语义解释 */
            expr: string;
            tz: string;
        };
        CronAuthorization: {
            tool: string;
            /** @description 与M2员工规则相同的规范化匹配语义；只能从当前合法候选范围缩小 */
            pattern: string;
        };
        CronTarget: {
            instruction: string;
            /** @enum {string} */
            execution_mode: "existing" | "new_conversation";
            /** @description existing必须为同工作区同员工可见会话；new_conversation必须为null */
            conversation_id: string | null;
        };
        CronCreateRequest: {
            change_id: string;
            workspace_id: string;
            agent_id: string;
            name: string;
            schedule: components["schemas"]["CronSchedule"];
            target: components["schemas"]["CronTarget"];
            pre_authorized: components["schemas"]["CronAuthorization"][];
            /** @default true */
            enabled: boolean;
        };
        CronPatchRequest: {
            change_id: string;
            expected_revision: number;
            name?: string;
            schedule?: components["schemas"]["CronSchedule"];
            target?: components["schemas"]["CronTarget"];
            pre_authorized?: components["schemas"]["CronAuthorization"][];
            enabled?: boolean;
        };
        CronJob: {
            id: string;
            workspace_id: string;
            name: string;
            revision: number;
            schedule: components["schemas"]["CronSchedule"];
            target: components["schemas"]["CronTarget"];
            metadata: {
                agent_id: string;
                agent_spec_snapshot: {
                    [key: string]: unknown;
                };
                skill_versions: {
                    [key: string]: string;
                };
                pre_authorized: components["schemas"]["CronAuthorization"][];
                /** @enum {string} */
                created_by: "user" | "agent";
                owner_id: string;
                credential_owner_id: string;
                created_via_task_run_id: string | null;
                proposal_id: string | null;
            };
            state: {
                enabled: boolean;
                /** @description UTC Unix毫秒 */
                next_run_at: number | null;
                last_run_at: number | null;
                /** @enum {string|null} */
                last_status: "fired" | "missed" | "skipped" | "failed" | "completed" | "interrupted" | "pending_verification" | "retry_wait" | "cancelled" | null;
                /** @description 初始派发的occurrence数量，不包含重试 */
                run_count: number;
                retry_count: number;
                /** @constant */
                max_retries: 3;
            };
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            updated_at: string;
            /** Format: date-time */
            deleted_at: string | null;
        };
        CronOccurrence: {
            id: string;
            job_id: string;
            revision: number;
            /** @enum {string} */
            trigger: "scheduled" | "manual";
            /** @description 仅定时发生使用，UNIQUE(job_id */
            scheduled_at: number | null;
            triggered_at: number;
            /** @description 手动发生使用UNIQUE(job_id */
            client_request_id: string | null;
            /** @enum {string} */
            status: "fired" | "missed" | "skipped" | "failed" | "completed" | "interrupted" | "pending_verification" | "retry_wait" | "cancelled";
            note: string | null;
            retry_count: number;
            retry_at: number | null;
            task_run_id: string | null;
            missed_count: number;
            missed_through: number | null;
            attempts: {
                retry_no: number;
                task_run_id: string;
                attempt_no: number;
                status: string;
                /** Format: date-time */
                started_at: string | null;
                /** Format: date-time */
                finished_at: string | null;
            }[];
        };
        EventReference: {
            task_run_id: string;
            attempt_no: number | null;
            seq: number;
            global_seq: number;
        };
        TraceReport: {
            id: string;
            job_id: string;
            task_run_id: string;
            /** @description 尚未开始实际尝试时保留null */
            attempt_no: number | null;
            source_global_seq: number;
            model: string;
            /** Format: date-time */
            created_at: string;
            report: {
                summary: string;
                /** @enum {string} */
                verdict: "good" | "needs_attention" | "failed_analyzed";
                root_cause: string | null;
                root_cause_event: null | components["schemas"]["EventReference"];
                improvement_suggestions: string[];
                anomalies: string[];
                skill_proposal: null | {
                    name: string;
                    description: string;
                    outline: string;
                    mechanism: string;
                    existing_skill_id?: string | null;
                };
            };
        };
        TraceJob: {
            id: string;
            /** @enum {string} */
            kind: "trace_audit" | "promote_skill";
            /** @enum {string} */
            status: "queued" | "running" | "completed" | "skipped" | "cancelled" | "interrupted" | "failed";
            task_run_id: string;
            /** @description 尚未开始实际尝试时保留null */
            attempt_no: number | null;
            trigger_global_seq: number;
            model: string | null;
            config_version: string;
            reason: string | null;
            report_id: string | null;
            skill_id: string | null;
            version_id?: string | null;
            /** @description 缺失usage保留null */
            usage: {
                [key: string]: unknown;
            } | null;
            /** @description 固化技能当前是否具有员工Grant */
            granted?: boolean;
            report_ids?: string[];
            targets?: {
                job_id: string;
                task_run_id: string;
                attempt_no: number | null;
                source_global_seq: number;
                result: string | null;
            }[];
        };
        PromoteSkillRequest: {
            client_request_id: string;
            /** @constant */
            confirmed: true;
            expected_report_id: string;
        };
        ScheduleProposal: {
            id: string;
            task_run_id: string;
            attempt_no: number;
            call_id: string;
            input_hash: string;
            revision: number;
            proposal: components["schemas"]["CronCreateRequest"];
            candidates: components["schemas"]["CronAuthorization"][];
            selected: components["schemas"]["CronAuthorization"][];
            /** @enum {string} */
            status: "pending" | "approved" | "rejected" | "expired";
            job_id: string | null;
            decided_by: string | null;
            /** Format: date-time */
            decided_at: string | null;
        };
        ScheduleProposalDecision: {
            /** @enum {string} */
            decision: "allow_once" | "reject_once";
            input_hash: string;
            expected_revision: number;
            selected: components["schemas"]["CronAuthorization"][];
        };
        Notification: {
            /** @description 持久唯一身份；重复补播和重启只查询同一记录 */
            id: string;
            global_seq: number;
            owner_id: string;
            workspace_id: string;
            agent_id: string;
            job_id: string | null;
            task_run_id: string | null;
            kind: string;
            /** @enum {string} */
            severity: "badge" | "warning" | "error";
            message: string;
            /** Format: date-time */
            read_at: string | null;
            /** Format: date-time */
            presented_at: string | null;
            /** Format: date-time */
            created_at: string;
        };
        DesktopPreferences: {
            close_to_quit: boolean;
            prevent_sleep: boolean;
        };
        ExitImpact: {
            active_runs: components["schemas"]["RunRecord"][];
            pending_verifications: components["schemas"]["PendingVerification"][];
            /** @description 真实当前时间之后24小时内到期的启用计划 */
            upcoming_jobs: components["schemas"]["CronJob"][];
            background_jobs: {
                [key: string]: unknown;
            }[];
            at_global_seq: number;
            /** Format: date-time */
            observed_at: string;
        };
    };
    responses: {
        /** @description 已持久化计划及实际修订 */
        CronJobEnvelope: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/json": {
                    data: components["schemas"]["CronJob"];
                };
            };
        };
        /** @description 实际辅助作业，202仅代表已受理 */
        TraceJobEnvelope: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/json": {
                    data: components["schemas"]["TraceJob"];
                };
            };
        };
        /** @description 真实资源及修订，凭据只返回configured状态 */
        GovernanceResourceEnvelope: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/json": {
                    data: components["schemas"]["GovernanceResource"];
                };
            };
        };
        /** @description 当前身份可见资源的稳定分页 */
        GovernanceResourcePage: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/json": {
                    data: {
                        items: components["schemas"]["GovernanceResource"][];
                        next_after: string | null;
                    };
                };
            };
        };
        /** @description 已提交完整变更，重试返回首次提交标识 */
        MemoryChangeEnvelope: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/json": {
                    data: components["schemas"]["MemoryChange"];
                };
            };
        };
        /** @description 真实后台作业及状态 */
        MemoryJobEnvelope: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/json": {
                    data: components["schemas"]["MemoryJob"];
                };
            };
        };
        /** @description 错误信封 */
        ErrEnvelope: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/json": components["schemas"]["Error"];
            };
        };
    };
    parameters: {
        GovernanceId: string;
        GovernanceWorkspace: string;
        GovernanceLimit: number;
        /** @description 排他游标绑定有效身份、范围、过滤及稳定排序 */
        GovernanceAfter: string;
        /** @description 服务端签发标识，未知或失效返回401；缺省使用真实owner */
        DemoIdentity: string;
        MemoryStoreType: "user" | "workspace" | "soul" | "skill";
        MemoryStoreId: string;
        MemoryLimit: number;
        /** @description 排他游标，绑定相同身份、范围、过滤及排序；不匹配返回422 */
        MemoryAfter: string;
        MemoryWorkspace: string;
        MemoryAgent: string;
    };
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    readIdentity: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 请求固定的有效身份及当前角色 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Identity"];
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    issueDemoIdentity: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DemoIdentityRequest"];
            };
        };
        responses: {
            /** @description 已认证真实owner签发的演示身份；返回标识仅用于该窗口 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: {
                            identity: components["schemas"]["Identity"];
                            /** @description 不得写入公开日志或证据 */
                            identity_token: string;
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    readOrganization: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            200: components["responses"]["GovernanceResourceEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    editOrganization: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GovernanceResourcePatch"];
            };
        };
        responses: {
            200: components["responses"]["GovernanceResourceEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listMemberships: {
        parameters: {
            query?: {
                limit?: components["parameters"]["GovernanceLimit"];
                /** @description 排他游标绑定有效身份、范围、过滤及稳定排序 */
                after?: components["parameters"]["GovernanceAfter"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description owner读取本组织；其他身份仅读取自身成员 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: {
                            items: components["schemas"]["Membership"][];
                            next_after: string | null;
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    editMembershipRole: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MembershipRoleRequest"];
            };
        };
        responses: {
            /** @description 已更新角色及成员状态；最后有效owner禁止降权或禁用 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Membership"];
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    editWorkspaceMembership: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
                user_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceMemberRequest"];
            };
        };
        responses: {
            /** @description owner登记或回收工作区访问 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: {
                            workspace_id: string;
                            user_id: string;
                            enabled: boolean;
                            revision: number;
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listWorkspaces: {
        parameters: {
            query?: {
                limit?: components["parameters"]["GovernanceLimit"];
                /** @description 排他游标绑定有效身份、范围、过滤及稳定排序 */
                after?: components["parameters"]["GovernanceAfter"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            200: components["responses"]["GovernanceResourcePage"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    createWorkspace: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceRequest"];
            };
        };
        responses: {
            200: components["responses"]["GovernanceResourceEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    readWorkspace: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            200: components["responses"]["GovernanceResourceEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    disableWorkspace: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GovernanceChangeRequest"];
            };
        };
        responses: {
            200: components["responses"]["GovernanceResourceEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    editWorkspace: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GovernanceResourcePatch"];
            };
        };
        responses: {
            200: components["responses"]["GovernanceResourceEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listAgents: {
        parameters: {
            query?: {
                workspace_id?: components["parameters"]["GovernanceWorkspace"];
                limit?: components["parameters"]["GovernanceLimit"];
                /** @description 排他游标绑定有效身份、范围、过滤及稳定排序 */
                after?: components["parameters"]["GovernanceAfter"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            200: components["responses"]["GovernanceResourcePage"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    createAgent: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AgentRequest"];
            };
        };
        responses: {
            200: components["responses"]["GovernanceResourceEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    readAgent: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            200: components["responses"]["GovernanceResourceEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    disableAgent: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GovernanceChangeRequest"];
            };
        };
        responses: {
            200: components["responses"]["GovernanceResourceEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    editAgent: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GovernanceResourcePatch"];
            };
        };
        responses: {
            200: components["responses"]["GovernanceResourceEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listGovernanceSkills: {
        parameters: {
            query?: {
                workspace_id?: components["parameters"]["GovernanceWorkspace"];
                limit?: components["parameters"]["GovernanceLimit"];
                /** @description 排他游标绑定有效身份、范围、过滤及稳定排序 */
                after?: components["parameters"]["GovernanceAfter"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            200: components["responses"]["GovernanceResourcePage"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    createGovernanceSkill: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GovernanceSkillCreate"];
            };
        };
        responses: {
            200: components["responses"]["GovernanceResourceEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    readGovernanceSkill: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            200: components["responses"]["GovernanceResourceEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    archiveGovernanceSkill: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GovernanceChangeRequest"];
            };
        };
        responses: {
            200: components["responses"]["GovernanceResourceEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    editGovernanceSkill: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GovernanceResourcePatch"];
            };
        };
        responses: {
            200: components["responses"]["GovernanceResourceEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listSkillVersions: {
        parameters: {
            query?: {
                limit?: components["parameters"]["GovernanceLimit"];
                /** @description 排他游标绑定有效身份、范围、过滤及稳定排序 */
                after?: components["parameters"]["GovernanceAfter"];
            };
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 不可变历史版本，按version_no降序 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: {
                            items: components["schemas"]["SkillVersion"][];
                            next_after: string | null;
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    publishSkillVersion: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SkillVersionRequest"];
            };
        };
        responses: {
            /** @description 版本正文与支撑文件；恢复旧正文产生新版本 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["SkillVersion"];
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    readSkillVersion: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
                version_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 版本正文与支撑文件；恢复旧正文产生新版本 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["SkillVersion"];
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listConnectors: {
        parameters: {
            query?: {
                workspace_id?: components["parameters"]["GovernanceWorkspace"];
                limit?: components["parameters"]["GovernanceLimit"];
                /** @description 排他游标绑定有效身份、范围、过滤及稳定排序 */
                after?: components["parameters"]["GovernanceAfter"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            200: components["responses"]["GovernanceResourcePage"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    createConnector: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ConnectorRequest"];
            };
        };
        responses: {
            200: components["responses"]["GovernanceResourceEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    readConnector: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            200: components["responses"]["GovernanceResourceEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    disableConnector: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GovernanceChangeRequest"];
            };
        };
        responses: {
            200: components["responses"]["GovernanceResourceEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    editConnector: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GovernanceResourcePatch"];
            };
        };
        responses: {
            200: components["responses"]["GovernanceResourceEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    validateConnector: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 真实服务响应或SDK工具发现；连接失败明确返回错误 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: {
                            connector_id: string;
                            ok: boolean;
                            status_code?: number;
                            tools: {
                                name: string;
                                parameters: {
                                    [key: string]: unknown;
                                };
                            }[];
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listGrants: {
        parameters: {
            query?: {
                workspace_id?: components["parameters"]["GovernanceWorkspace"];
                limit?: components["parameters"]["GovernanceLimit"];
                /** @description 排他游标绑定有效身份、范围、过滤及稳定排序 */
                after?: components["parameters"]["GovernanceAfter"];
                revoked?: boolean;
                grantee_id?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 当前身份范围内授权及撤销状态 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: {
                            items: components["schemas"]["Grant"][];
                            next_after: string | null;
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    createGrant: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GrantRequest"];
            };
        };
        responses: {
            /** @description 持久化授权及修订，相同请求返回首次记录 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Grant"];
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    revokeGrant: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GovernanceChangeRequest"];
            };
        };
        responses: {
            /** @description 持久化授权及修订，相同请求返回首次记录 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Grant"];
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listPermissionRules: {
        parameters: {
            query?: {
                limit?: components["parameters"]["GovernanceLimit"];
                /** @description 排他游标绑定有效身份、范围、过滤及稳定排序 */
                after?: components["parameters"]["GovernanceAfter"];
                revoked?: boolean;
            };
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 员工授权规则及来源，deny优先 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: {
                            items: components["schemas"]["PermissionRule"][];
                            next_after: string | null;
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    createPermissionRule: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PermissionRuleRequest"];
            };
        };
        responses: {
            /** @description 已保存规范化规则，人工规则不扩大强制边界 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["PermissionRule"];
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    revokePermissionRule: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
                rule_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GovernanceChangeRequest"];
            };
        };
        responses: {
            /** @description 已保存规范化规则，人工规则不扩大强制边界 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["PermissionRule"];
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    streamGovernanceEvents: {
        parameters: {
            query?: {
                workspace_id?: components["parameters"]["GovernanceWorkspace"];
                from?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description text/event-stream，当前身份可见范围；撤权结束订阅，沿用排他global_seq与控制帧 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listAudit: {
        parameters: {
            query?: {
                workspace_id?: components["parameters"]["GovernanceWorkspace"];
                limit?: components["parameters"]["GovernanceLimit"];
                /** @description 排他游标绑定有效身份、范围、过滤及稳定排序 */
                after?: components["parameters"]["GovernanceAfter"];
                actor?: string;
                action?: string;
                /** @description resource_type或resource_type:resource_id */
                resource?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 脱敏审计记录按seq降序；member仅自身actor */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: {
                            items: components["schemas"]["AuditRecord"][];
                            next_after: string | null;
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    exportAudit: {
        parameters: {
            query?: {
                workspace_id?: components["parameters"]["GovernanceWorkspace"];
                limit?: components["parameters"]["GovernanceLimit"];
                /** @description 排他游标绑定有效身份、范围、过滤及稳定排序 */
                after?: components["parameters"]["GovernanceAfter"];
                actor?: string;
                action?: string;
                /** @description resource_type或resource_type:resource_id */
                resource?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 脱敏审计记录按seq降序；member仅自身actor */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: {
                            items: components["schemas"]["AuditRecord"][];
                            next_after: string | null;
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    verifyAudit: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description owner执行两级校验；失败进入只读诊断，损坏链禁止追加验证审计 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["AuditVerification"];
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listDiagnosticBackups: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 服务管理的备份；恢复前重新验证SHA及两级审计 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["DiagnosticBackup"][];
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    createDiagnosticBackup: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    change_id: string;
                    /** @enum {string} */
                    kind: "database" | "directory";
                };
            };
        };
        responses: {
            /** @description 正常模式创建并验证备份；诊断期间禁止创建 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["DiagnosticBackup"];
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    restoreDiagnosticBackup: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    backup_id: string;
                    change_id: string;
                };
            };
        };
        responses: {
            /** @description 受控恢复保留损坏材料；重启并通过完整验证后才能恢复业务 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: {
                            backup_id: string;
                            /** @constant */
                            restart_required: true;
                            preserved_path: string;
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    readRunEvents: {
        parameters: {
            query?: {
                from?: number;
                after_seq?: number;
                limit?: number;
                /** @description 首次读取返回的快照水位；后续页面固定上界 */
                through_global_seq?: number;
                /** @description 只读取历史快照水位之后的真实尾部，与任务seq和固定上界共同约束 */
                after_global_seq?: number;
                attempt_no?: number;
            };
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 未指定limit时为SSE；JSON读取使用固定水位和排他任务seq */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/event-stream": string;
                    "application/json": {
                        data: {
                            items: components["schemas"]["RunEvent"][];
                            next_after_seq: number | null;
                            at_global_seq: number;
                            has_more: boolean;
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    readMemoryStore: {
        parameters: {
            query: {
                workspace_id: components["parameters"]["MemoryWorkspace"];
                agent_id: components["parameters"]["MemoryAgent"];
                state?: "all" | "active" | "stale" | "archived" | "pinned";
                limit?: components["parameters"]["MemoryLimit"];
                /** @description 排他游标，绑定相同身份、范围、过滤及排序；不匹配返回422 */
                after?: components["parameters"]["MemoryAfter"];
            };
            header?: never;
            path: {
                store_type: components["parameters"]["MemoryStoreType"];
                store_id: components["parameters"]["MemoryStoreId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 已核对身份范围的原文和 metadata；未完成意图返回503 STORE_RECOVERING */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["MemoryStore"] & {
                            next_after?: string | null;
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    createMemoryEntry: {
        parameters: {
            query: {
                workspace_id: components["parameters"]["MemoryWorkspace"];
                agent_id: components["parameters"]["MemoryAgent"];
            };
            header?: never;
            path: {
                store_type: components["parameters"]["MemoryStoreType"];
                store_id: components["parameters"]["MemoryStoreId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MemoryWriteRequest"];
            };
        };
        responses: {
            200: components["responses"]["MemoryChangeEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    archiveMemoryEntry: {
        parameters: {
            query: {
                workspace_id: components["parameters"]["MemoryWorkspace"];
                agent_id: components["parameters"]["MemoryAgent"];
            };
            header?: never;
            path: {
                store_type: components["parameters"]["MemoryStoreType"];
                store_id: components["parameters"]["MemoryStoreId"];
                entry_hash: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MemoryChangeRequest"];
            };
        };
        responses: {
            200: components["responses"]["MemoryChangeEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    editMemoryEntry: {
        parameters: {
            query: {
                workspace_id: components["parameters"]["MemoryWorkspace"];
                agent_id: components["parameters"]["MemoryAgent"];
            };
            header?: never;
            path: {
                store_type: components["parameters"]["MemoryStoreType"];
                store_id: components["parameters"]["MemoryStoreId"];
                entry_hash: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MemoryWriteRequest"];
            };
        };
        responses: {
            200: components["responses"]["MemoryChangeEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    changeMemoryEntryState: {
        parameters: {
            query: {
                workspace_id: components["parameters"]["MemoryWorkspace"];
                agent_id: components["parameters"]["MemoryAgent"];
            };
            header?: never;
            path: {
                store_type: components["parameters"]["MemoryStoreType"];
                store_id: components["parameters"]["MemoryStoreId"];
                entry_hash: string;
                action: "pin" | "unpin" | "archive" | "restore";
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MemoryChangeRequest"];
            };
        };
        responses: {
            200: components["responses"]["MemoryChangeEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    reviewMemoryEntry: {
        parameters: {
            query: {
                workspace_id: components["parameters"]["MemoryWorkspace"];
                agent_id: components["parameters"]["MemoryAgent"];
            };
            header?: never;
            path: {
                store_type: components["parameters"]["MemoryStoreType"];
                store_id: components["parameters"]["MemoryStoreId"];
                entry_hash: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MemoryReviewRequest"];
            };
        };
        responses: {
            200: components["responses"]["MemoryChangeEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listMemoryLedger: {
        parameters: {
            query: {
                workspace_id: components["parameters"]["MemoryWorkspace"];
                agent_id: components["parameters"]["MemoryAgent"];
                store_type: "user" | "workspace" | "soul" | "skill";
                store_id: string;
                limit?: components["parameters"]["MemoryLimit"];
                /** @description 排他游标，绑定相同身份、范围、过滤及排序；不匹配返回422 */
                after?: components["parameters"]["MemoryAfter"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 不可修改的账本记录 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: {
                            items: components["schemas"]["MemoryLedger"][];
                            next_after: string | null;
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    restoreMemoryLedger: {
        parameters: {
            query: {
                workspace_id: components["parameters"]["MemoryWorkspace"];
                agent_id: components["parameters"]["MemoryAgent"];
            };
            header?: never;
            path: {
                id: number;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MemoryChangeRequest"];
            };
        };
        responses: {
            200: components["responses"]["MemoryChangeEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    searchMemory: {
        parameters: {
            query: {
                query: string;
                workspace_id: components["parameters"]["MemoryWorkspace"];
                agent_id: components["parameters"]["MemoryAgent"];
                archived?: boolean;
                limit?: components["parameters"]["MemoryLimit"];
                /** @description 排他游标，绑定相同身份、范围、过滤及排序；不匹配返回422 */
                after?: components["parameters"]["MemoryAfter"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description trigram 或短查询参数化子串结果，按 created_at、id 降序 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: {
                            items: components["schemas"]["MemorySearchHit"][];
                            next_after: string | null;
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listMemorySkills: {
        parameters: {
            query: {
                workspace_id: components["parameters"]["MemoryWorkspace"];
                agent_id: components["parameters"]["MemoryAgent"];
                archived?: boolean;
                limit?: components["parameters"]["MemoryLimit"];
                /** @description 排他游标，绑定相同身份、范围、过滤及排序；不匹配返回422 */
                after?: components["parameters"]["MemoryAfter"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 按名称、标识正序分页 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: {
                            items: components["schemas"]["SkillIndexItem"][];
                            next_after: string | null;
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    createMemorySkill: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SkillCreateRequest"];
            };
        };
        responses: {
            /** @description 已创建完整 Skill，重复 change_id 返回首次分配的 store_id */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["MemoryChange"] & {
                            store_id: string;
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    readMemorySkillFile: {
        parameters: {
            query: {
                workspace_id: components["parameters"]["MemoryWorkspace"];
                agent_id: components["parameters"]["MemoryAgent"];
                file: string;
            };
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 原始正文与可校验修订 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: {
                            file: string;
                            text: string;
                            revision: number;
                            sha256: string;
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listMemoryJobs: {
        parameters: {
            query: {
                workspace_id: components["parameters"]["MemoryWorkspace"];
                agent_id: components["parameters"]["MemoryAgent"];
                conversation_id?: string;
                limit?: components["parameters"]["MemoryLimit"];
                /** @description 排他游标，绑定相同身份、范围、过滤及排序；不匹配返回422 */
                after?: components["parameters"]["MemoryAfter"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 后台作业按 created_at、id 降序，含独立审批、usage 与真实错误 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: {
                            items: components["schemas"]["MemoryJob"][];
                            next_after: string | null;
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    readMemoryJob: {
        parameters: {
            query?: {
                /** @description 可选当前范围，必须与 agent_id 同时提供并匹配作业 */
                workspace_id?: string;
                agent_id?: string;
            };
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            200: components["responses"]["MemoryJobEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    cancelMemoryJob: {
        parameters: {
            query?: {
                /** @description 可选当前范围，必须与 agent_id 同时提供并匹配作业 */
                workspace_id?: string;
                agent_id?: string;
            };
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            200: components["responses"]["MemoryJobEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    decideMemoryJobApproval: {
        parameters: {
            query?: {
                /** @description 可选当前范围，必须与 agent_id 同时提供并匹配作业 */
                workspace_id?: string;
                agent_id?: string;
            };
            header?: never;
            path: {
                id: string;
                approval_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    /** @enum {string} */
                    decision: "allow_once" | "reject_once";
                    input_hash: string;
                };
            };
        };
        responses: {
            200: components["responses"]["MemoryJobEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    startMemoryCurator: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    workspace_id: string;
                    agent_id: string;
                    client_request_id: string;
                };
            };
        };
        responses: {
            202: components["responses"]["MemoryJobEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    streamMemoryEvents: {
        parameters: {
            query: {
                workspace_id: components["parameters"]["MemoryWorkspace"];
                agent_id: components["parameters"]["MemoryAgent"];
                from?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description text/event-stream，既有事件信封及 resync/shutdown 控制帧，无 JSON 信封 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listCronJobs: {
        parameters: {
            query?: {
                workspace_id?: components["parameters"]["GovernanceWorkspace"];
                limit?: components["parameters"]["GovernanceLimit"];
                /** @description 排他游标绑定有效身份、范围、过滤及稳定排序 */
                after?: components["parameters"]["GovernanceAfter"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 可见计划按created_at、id降序排他分页 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: {
                            items: components["schemas"]["CronJob"][];
                            next_after: string | null;
                            at_global_seq: number;
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    createCronJob: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CronCreateRequest"];
            };
        };
        responses: {
            201: components["responses"]["CronJobEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    readCronJob: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            200: components["responses"]["CronJobEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    deleteCronJob: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GovernanceChangeRequest"];
            };
        };
        responses: {
            200: components["responses"]["CronJobEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    editCronJob: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CronPatchRequest"];
            };
        };
        responses: {
            200: components["responses"]["CronJobEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    runCronJobNow: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    client_request_id: string;
                    expected_revision: number;
                };
            };
        };
        responses: {
            /** @description 已登记独立手动发生，仍经过无人值守权限与冲突检查 */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CronOccurrence"];
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listCronOccurrences: {
        parameters: {
            query?: {
                limit?: components["parameters"]["GovernanceLimit"];
                /** @description 排他游标绑定有效身份、范围、过滤及稳定排序 */
                after?: components["parameters"]["GovernanceAfter"];
            };
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 发生记录与重试关联按triggered_at、id降序；删除计划保留历史 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: {
                            items: components["schemas"]["CronOccurrence"][];
                            next_after: string | null;
                            at_global_seq: number;
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    previewCronAuthorization: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CronCreateRequest"];
            };
        };
        responses: {
            /** @description 当前角色、Grant、scope、protected与员工规则限定的候选范围，预览不授予权限 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: {
                            candidates: components["schemas"]["CronAuthorization"][];
                            next_run_at: number | null;
                            authorization_sha256: string;
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    readScheduleProposal: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 持久不可变员工提案及人工选择 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["ScheduleProposal"];
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    decideScheduleProposal: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ScheduleProposalDecision"];
            };
        };
        responses: {
            /** @description 持久不可变员工提案及人工选择 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["ScheduleProposal"];
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    streamCronEvents: {
        parameters: {
            query?: {
                workspace_id?: components["parameters"]["GovernanceWorkspace"];
                from?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description text/event-stream；作业域global_seq排他，先注册后补读，当前身份逐次复查 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listRunCenterRuns: {
        parameters: {
            query?: {
                workspace_id?: components["parameters"]["GovernanceWorkspace"];
                limit?: components["parameters"]["GovernanceLimit"];
                /** @description 排他游标绑定有效身份、范围、过滤及稳定排序 */
                after?: components["parameters"]["GovernanceAfter"];
                agent_id?: string;
                conversation_id?: string;
                status?: "queued" | "running" | "waiting_user" | "waiting_verification" | "interrupted" | "completed" | "failed" | "cancelled";
                source?: "user" | "cron" | "manual";
                since?: string;
                until?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 同一SQL读取快照内取得记录与水位；created_at、id降序且游标绑定身份和过滤 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: {
                            items: components["schemas"]["RunRecord"][];
                            next_after: string | null;
                            at_global_seq: number;
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    readRunMetrics: {
        parameters: {
            query?: {
                workspace_id?: components["parameters"]["GovernanceWorkspace"];
                limit?: components["parameters"]["GovernanceLimit"];
                /** @description 排他游标绑定有效身份、范围、过滤及稳定排序 */
                after?: components["parameters"]["GovernanceAfter"];
                agent_id?: string;
                conversation_id?: string;
                status?: "queued" | "running" | "waiting_user" | "waiting_verification" | "interrupted" | "completed" | "failed" | "cancelled";
                source?: "user" | "cron" | "manual";
                since?: string;
                until?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 各表分别聚合的SQL指标，同一可见任务集合，无一对多重复累计 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["RunMetrics"];
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    readRunCenterRun: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 可见运行及历史员工配置、技能版本 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["RunRecord"];
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    locateRunEvent: {
        parameters: {
            query?: {
                attempt_no?: number;
            };
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
                seq: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 精确定位原始任务事件；尝试不匹配或事件缺失返回404 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["RunEvent"];
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    readRunCall: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
                call_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 真实模型请求摘要与工具明细，保留声明，去除凭据及隐藏推理 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["RunCall"];
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    readTraceReport: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 当前适用尝试的模型报告及实际作业状态；无报告返回null */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: {
                            report: null | components["schemas"]["TraceReport"];
                            job: null | components["schemas"]["TraceJob"];
                            applicable: boolean;
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    requestTraceReview: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    client_request_id: string;
                    /** @description 真人针对实际材料明确提出的分析要求，绑定持久审查请求 */
                    instruction?: string | null;
                };
            };
        };
        responses: {
            202: components["responses"]["TraceJobEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listTraceJobs: {
        parameters: {
            query?: {
                workspace_id?: components["parameters"]["GovernanceWorkspace"];
                limit?: components["parameters"]["GovernanceLimit"];
                /** @description 排他游标绑定有效身份、范围、过滤及稳定排序 */
                after?: components["parameters"]["GovernanceAfter"];
                agent_id?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 当前身份可见的实际审查和技能固化作业 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: {
                            items: components["schemas"]["TraceJob"][];
                            next_after: string | null;
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    readTraceJob: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            200: components["responses"]["TraceJobEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    cancelTraceJob: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            200: components["responses"]["TraceJobEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    promoteTraceSkill: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PromoteSkillRequest"];
            };
        };
        responses: {
            202: components["responses"]["TraceJobEnvelope"];
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listNotifications: {
        parameters: {
            query?: {
                workspace_id?: components["parameters"]["GovernanceWorkspace"];
                limit?: components["parameters"]["GovernanceLimit"];
                /** @description 排他游标绑定有效身份、范围、过滤及稳定排序 */
                after?: components["parameters"]["GovernanceAfter"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 当前授权范围内的持久通知，按global_seq、id降序排他分页 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: {
                            items: components["schemas"]["Notification"][];
                            next_after: string | null;
                            unread_count: number;
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    acknowledgeNotification: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: components["parameters"]["GovernanceId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    /**
                     * @description presented以持久compare-and-set认领，仅首次响应claimed=true允许弹出
                     * @enum {string}
                     */
                    action: "read" | "presented";
                };
            };
        };
        responses: {
            /** @description 幂等标记与是否首次认领 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: {
                            notification: components["schemas"]["Notification"];
                            claimed: boolean;
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    readExitImpact: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 真实生命周期影响的SQL快照 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["ExitImpact"];
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    recordPowerEvent: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    /** @enum {string} */
                    event: "suspend" | "resume";
                    /** Format: date-time */
                    observed_at: string;
                };
            };
        };
        responses: {
            /** @description Electron powerMonitor实际事件已持久化，resume完成调度重新核查 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: {
                            global_seq: number;
                        };
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    readDesktopPreferences: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 实际保存的桌面行为 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["DesktopPreferences"];
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    editDesktopPreferences: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DesktopPreferences"];
            };
        };
        responses: {
            /** @description 实际保存的桌面行为 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["DesktopPreferences"];
                    };
                };
            };
            /** @description 错误信封 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
            /** @description 错误信封 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
}
