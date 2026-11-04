import 'package:editor_23/build.dart';
import 'package:editor_23/models/capability_status.dart';
import 'package:editor_23/services/backend_client.dart';
import 'package:editor_23/theme/app_theme.dart';
import 'package:editor_23/widgets/fade_in.dart';
import 'package:flutter/material.dart';

/// Truthful AI capability status. Every row comes from the backend's live
/// /status endpoint; nothing here is hard-coded as working.
class StatusScreen extends StatefulWidget {
  /// Injected backend client (tests). Defaults to a real [BackendClient].
  final BackendClient? client;

  const StatusScreen({super.key, this.client});

  @override
  State<StatusScreen> createState() => _StatusScreenState();
}

class _StatusScreenState extends State<StatusScreen> {
  late final BackendClient _client;
  BackendStatus? _status;
  String? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _client = widget.client ?? BackendClient();
    _refresh();
  }

  @override
  void dispose() {
    _client.close();
    super.dispose();
  }

  Future<void> _refresh() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final Map<String, dynamic> json = await _client.getStatus();
      if (!mounted) {
        return;
      }
      setState(() {
        _status = BackendStatus.fromJson(json);
        _loading = false;
      });
    } on BackendException catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _error = error.message;
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final TextTheme text = Theme.of(context).textTheme;
    return Scaffold(
      appBar: AppBar(
        title: const Text('AI Status'),
        actions: [
          IconButton(
            onPressed: _loading ? null : _refresh,
            tooltip: 'Refresh status',
            icon: const Icon(Icons.refresh_outlined),
          ),
        ],
      ),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              FadeIn(
                child: Card(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Text('Backend', style: text.titleMedium),
                        const SizedBox(height: 8),
                        SelectableText(
                          BackendClient.baseUrl,
                          style: text.bodyMedium?.copyWith(
                            color: AppColors.violetSoft,
                          ),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          'Run "python server.py" in backend/ on this '
                          'machine. A USB-connected phone uses '
                          'http://127.0.0.1:8080 with adb reverse; other '
                          'devices need your computer\'s LAN address via '
                          '--dart-define=BACKEND_URL=...',
                          style: text.bodySmall,
                        ),
                        const SizedBox(height: 8),
                        Text(
                          'App build $kAppBuild'
                          '${_status == null || _status!.serverBuild.isEmpty ? '' : ' • Backend build ${_status!.serverBuild}'}. '
                          'Check these against the current source before '
                          'judging a phone failure.',
                          style: text.bodySmall,
                        ),
                      ],
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 16),
              if (_loading)
                const Card(
                  child: Padding(
                    padding: EdgeInsets.symmetric(vertical: 32),
                    child: Center(child: CircularProgressIndicator()),
                  ),
                )
              else if (_error != null)
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(20),
                    child: Column(
                      children: <Widget>[
                        const Icon(
                          Icons.cloud_off_outlined,
                          size: 32,
                          color: AppColors.violetSoft,
                        ),
                        const SizedBox(height: 12),
                        Text(
                          'Backend is not connected.',
                          style: text.titleMedium,
                          textAlign: TextAlign.center,
                        ),
                        const SizedBox(height: 8),
                        Text(
                          _error!,
                          style: text.bodySmall,
                          textAlign: TextAlign.center,
                        ),
                        const SizedBox(height: 16),
                        OutlinedButton.icon(
                          onPressed: _refresh,
                          icon: const Icon(Icons.refresh_outlined),
                          label: const Text('Try again'),
                        ),
                      ],
                    ),
                  ),
                )
              else if (_status != null) ...<Widget>[
                _ConnectionCard(status: _status!),
                const SizedBox(height: 12),
                for (final CapabilityInfo info in _status!.integrations) ...[
                  _CapabilityCard(info: info),
                  const SizedBox(height: 12),
                ],
                Text(
                  '"Verified" means a real call succeeded — never that the '
                  'code merely exists. Verified dates persist on the '
                  'backend across restarts.',
                  style: text.bodySmall,
                  textAlign: TextAlign.center,
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// Backend reachability, credentials, model access, and FFmpeg — the
/// four facts that decide what every integration row below can do.
class _ConnectionCard extends StatelessWidget {
  final BackendStatus status;

  const _ConnectionCard({required this.status});

  String _accessLabel() {
    return switch (status.apiAccess) {
      'verified' => 'Available',
      'auth_error' => 'Denied — the API key was rejected',
      'unreachable' => 'Unreachable from the backend',
      'no_key' => 'No API key configured',
      _ => status.apiAccess,
    };
  }

  @override
  Widget build(BuildContext context) {
    final TextTheme text = Theme.of(context).textTheme;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text('Connection', style: text.titleSmall),
            const SizedBox(height: 8),
            Text(
              'Backend: reachable (this screen just loaded live data).',
              style: text.bodySmall,
            ),
            const SizedBox(height: 4),
            Text(
              status.apiKeyPresent
                  ? 'API key: configured.'
                  : 'API key: missing — add META_API_KEY to backend/.env '
                        'and restart the backend.',
              style: text.bodySmall,
            ),
            const SizedBox(height: 4),
            Text('Meta API access: ${_accessLabel()}.', style: text.bodySmall),
            const SizedBox(height: 4),
            Text(
              status.ffmpegPresent
                  ? 'FFmpeg: available'
                        '${status.ffmpegVersion.isEmpty ? '' : ' (${status.ffmpegVersion})'}.'
                  : 'FFmpeg: missing — rendering needs it on PATH.',
              style: text.bodySmall,
            ),
          ],
        ),
      ),
    );
  }
}

class _CapabilityCard extends StatelessWidget {
  final CapabilityInfo info;

  const _CapabilityCard({required this.info});

  @override
  Widget build(BuildContext context) {
    final TextTheme text = Theme.of(context).textTheme;
    final Color chipColor = switch (info.state) {
      CapabilityState.verified => const Color(0xFF34D399),
      CapabilityState.implementedButUntested => AppColors.violetSoft,
      CapabilityState.unavailable => AppColors.muted,
      CapabilityState.planned => AppColors.muted,
      CapabilityState.providerError => const Color(0xFFF59E0B),
    };
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Icon(info.state.icon, size: 22, color: chipColor),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(info.title, style: text.titleSmall),
                  const SizedBox(height: 4),
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 10,
                      vertical: 4,
                    ),
                    decoration: BoxDecoration(
                      color: chipColor.withValues(alpha: 0.15),
                      borderRadius: BorderRadius.circular(999),
                    ),
                    child: Text(
                      info.state.label,
                      style: text.bodySmall?.copyWith(
                        color: chipColor,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                  if (info.detail.isNotEmpty) ...<Widget>[
                    const SizedBox(height: 8),
                    Text(info.detail, style: text.bodySmall),
                  ],
                  // The last success stays visible beside any current
                  // state, including a newer provider error.
                  if ((info.verifiedAt ?? '').isNotEmpty) ...<Widget>[
                    const SizedBox(height: 4),
                    Text(
                      'Last live success: ${info.verifiedAt}',
                      style: text.bodySmall,
                    ),
                  ],
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
