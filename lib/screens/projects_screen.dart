import 'dart:io';

import 'package:editor_23/models/edit_setup.dart';
import 'package:editor_23/models/project.dart';
import 'package:editor_23/models/video_selection.dart';
import 'package:editor_23/screens/workspace_screen.dart';
import 'package:editor_23/services/project_store.dart';
import 'package:editor_23/widgets/empty_state.dart';
import 'package:editor_23/widgets/fade_in.dart';
import 'package:flutter/material.dart';

/// Saved projects on this device. Tapping a project reopens it in the
/// workspace with its settings, plan, and last output (when the files
/// still exist).
class ProjectsScreen extends StatefulWidget {
  final VoidCallback onCreateVideo;
  final ProjectStore store;

  const ProjectsScreen({
    super.key,
    required this.onCreateVideo,
    required this.store,
  });

  @override
  State<ProjectsScreen> createState() => _ProjectsScreenState();
}

class _ProjectsScreenState extends State<ProjectsScreen> {
  // Starts empty (never a spinner): the list appears as soon as the
  // local load finishes, which is instant on device.
  List<Project> _projects = const <Project>[];

  @override
  void initState() {
    super.initState();
    _reload();
  }

  Future<void> _reload() async {
    final List<Project> projects = await widget.store.listProjects();
    if (mounted) {
      setState(() => _projects = projects);
    }
  }

  String _statusLabel(String status) => switch (status) {
    'rendered' => 'Edited',
    'planned' => 'Planned',
    _ => 'Draft',
  };

  String _updatedLabel(DateTime updated) {
    final DateTime local = updated.toLocal();
    final String day = '${local.month}/${local.day}/${local.year}';
    final String minute = local.minute.toString().padLeft(2, '0');
    return '$day ${local.hour}:$minute';
  }

  void _open(Project project) {
    if (!File(project.sourcePath).existsSync()) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            'Source video is missing: ${project.fileName}. The project '
            'is kept, but it cannot reopen without the file.',
          ),
        ),
      );
      return;
    }
    Navigator.of(context)
        .push(
          MaterialPageRoute<void>(
            builder: (_) => WorkspaceScreen(
              video: VideoSelection(project.sourcePath),
              setup: EditSetup(
                instruction: project.instruction,
                style: project.style,
                aspectRatio: project.aspectRatio,
                fit: project.fit,
              ),
              onChanged: (_) {},
              projectStore: widget.store,
              project: project,
            ),
          ),
        )
        .then((_) => _reload());
  }

  @override
  Widget build(BuildContext context) {
    final List<Project> projects = _projects;
    return Scaffold(
      appBar: AppBar(title: const Text('Projects')),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(20),
          child: FadeIn(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: <Widget>[
                if (projects.isEmpty)
                  EmptyState(
                    icon: Icons.folder_open_outlined,
                    title: 'No projects yet',
                    message:
                        'Nothing is saved on this device yet. Start a new video '
                        'to create your first project.',
                    actionLabel: 'Create Video',
                    onAction: widget.onCreateVideo,
                  )
                else
                  for (final Project project in projects) ...<Widget>[
                    Card(
                      child: ListTile(
                        leading: const Icon(Icons.movie_outlined),
                        title: Text(project.name),
                        subtitle: Text(
                          '${_statusLabel(project.status)} • '
                          '${_updatedLabel(project.updatedAt)}'
                          '${project.instruction.isEmpty ? '' : '\n${project.instruction}'}',
                          maxLines: 3,
                          overflow: TextOverflow.ellipsis,
                        ),
                        trailing: project.renderPath == null
                            ? null
                            : const Icon(Icons.check_circle_outline),
                        onTap: () => _open(project),
                      ),
                    ),
                    const SizedBox(height: 8),
                  ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}
