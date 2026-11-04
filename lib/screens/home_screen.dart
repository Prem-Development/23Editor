import 'package:editor_23/models/project.dart';
import 'package:editor_23/services/project_store.dart';
import 'package:editor_23/theme/app_theme.dart';
import 'package:editor_23/widgets/empty_state.dart';
import 'package:editor_23/widgets/fade_in.dart';
import 'package:editor_23/widgets/primary_button.dart';
import 'package:flutter/material.dart';

/// Landing screen: brand, the main action, the real workflow, and
/// recent projects from the same store the Projects tab reads.
class HomeScreen extends StatefulWidget {
  final VoidCallback onCreateVideo;
  final ProjectStore store;
  final ValueChanged<Project> onOpenProject;

  /// Bumped by the shell whenever the Home tab is selected, so the
  /// recent list refreshes after editing elsewhere.
  final int refreshToken;

  const HomeScreen({
    super.key,
    required this.onCreateVideo,
    required this.store,
    required this.onOpenProject,
    this.refreshToken = 0,
  });

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  List<Project> _recent = const <Project>[];

  @override
  void initState() {
    super.initState();
    _reload();
  }

  @override
  void didUpdateWidget(covariant HomeScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.refreshToken != widget.refreshToken) {
      _reload();
    }
  }

  Future<void> _reload() async {
    final List<Project> projects = await widget.store.listProjects();
    if (mounted) {
      setState(() => _recent = projects.take(3).toList());
    }
  }

  String _statusLabel(String status) => switch (status) {
    'rendered' => 'Edited',
    'planned' => 'Planned',
    _ => 'Draft',
  };

  @override
  Widget build(BuildContext context) {
    final TextTheme text = Theme.of(context).textTheme;
    return Scaffold(
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              FadeIn(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: <Widget>[
                    Row(
                      children: <Widget>[
                        Container(
                          width: 56,
                          height: 56,
                          decoration: BoxDecoration(
                            gradient: AppColors.heroGradient,
                            borderRadius: BorderRadius.circular(16),
                          ),
                          child: const Icon(
                            Icons.movie_outlined,
                            color: Colors.white,
                            size: 28,
                          ),
                        ),
                        const SizedBox(width: 14),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: <Widget>[
                              Text('23Editor', style: text.headlineSmall),
                              Text(
                                'Your footage. Your story.',
                                style: text.bodyMedium?.copyWith(
                                  color: AppColors.violetSoft,
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 16),
                    Text(
                      'Choose a clip. Tell us your vision.',
                      style: text.bodyLarge,
                    ),
                    const SizedBox(height: 4),
                    Text(
                      'Pick a video, describe the edit in words, review '
                      'the editing plan, and render a real edited video.',
                      style: text.bodySmall,
                    ),
                    const SizedBox(height: 20),
                    PrimaryButton(
                      label: 'Create Video',
                      icon: Icons.video_call_outlined,
                      onPressed: widget.onCreateVideo,
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 20),
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: <Widget>[
                      Text('How it works', style: text.titleMedium),
                      const SizedBox(height: 12),
                      _StepRow(
                        icon: Icons.video_library_outlined,
                        title: 'Choose a clip',
                        message: 'Pick a real video from your device.',
                      ),
                      const SizedBox(height: 12),
                      _StepRow(
                        icon: Icons.edit_outlined,
                        title: 'Describe your vision',
                        message:
                            "Say or type how you'd like it edited — trim, "
                            'text, zoom, captions, music.',
                      ),
                      const SizedBox(height: 12),
                      _StepRow(
                        icon: Icons.fact_check_outlined,
                        title: 'Review the edits to apply',
                        message:
                            'Check every edit and where it came from before '
                            'anything renders.',
                      ),
                      const SizedBox(height: 12),
                      _StepRow(
                        icon: Icons.movie_creation_outlined,
                        title: 'Generate and share',
                        message:
                            'Render the edited video, preview it, and '
                            'share it from your device.',
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 20),
              if (_recent.isEmpty)
                const EmptyState(
                  icon: Icons.folder_open_outlined,
                  title: 'No projects yet',
                  message:
                      'Start your first video above and it will show up here.',
                )
              else ...<Widget>[
                Text('Recent projects', style: text.titleMedium),
                const SizedBox(height: 8),
                for (final Project project in _recent) ...<Widget>[
                  Card(
                    child: ListTile(
                      leading: const Icon(Icons.movie_outlined),
                      title: Text(project.name),
                      subtitle: Text(
                        '${_statusLabel(project.status)}'
                        '${project.instruction.isEmpty ? '' : ' • ${project.instruction}'}',
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                      ),
                      trailing: project.renderPath == null
                          ? null
                          : const Icon(Icons.check_circle_outline),
                      onTap: () => widget.onOpenProject(project),
                    ),
                  ),
                  const SizedBox(height: 8),
                ],
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _StepRow extends StatelessWidget {
  final IconData icon;
  final String title;
  final String message;

  const _StepRow({
    required this.icon,
    required this.title,
    required this.message,
  });

  @override
  Widget build(BuildContext context) {
    final TextTheme text = Theme.of(context).textTheme;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Container(
          padding: const EdgeInsets.all(10),
          decoration: const BoxDecoration(
            color: AppColors.surfaceHigh,
            shape: BoxShape.circle,
          ),
          child: Icon(icon, size: 20, color: AppColors.violetSoft),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text(title, style: text.titleSmall),
              const SizedBox(height: 2),
              Text(message, style: text.bodySmall),
            ],
          ),
        ),
      ],
    );
  }
}
