import 'dart:io';

import 'package:editor_23/models/edit_setup.dart';
import 'package:editor_23/models/project.dart';
import 'package:editor_23/models/video_selection.dart';
import 'package:editor_23/screens/create_screen.dart';
import 'package:editor_23/screens/home_screen.dart';
import 'package:editor_23/screens/projects_screen.dart';
import 'package:editor_23/screens/status_screen.dart';
import 'package:editor_23/screens/workspace_screen.dart';
import 'package:editor_23/services/project_store.dart';
import 'package:editor_23/theme/app_theme.dart';
import 'package:flutter/material.dart';

void main() {
  runApp(const MyApp());
}

/// Root of 23Editor: dark theme plus bottom-tab navigation.
class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = buildAppTheme();
    return MaterialApp(
      title: '23Editor',
      debugShowCheckedModeBanner: false,
      theme: theme,
      darkTheme: theme,
      themeMode: ThemeMode.dark,
      home: const RootShell(),
    );
  }
}

/// Bottom-navigation shell hosting Home and Projects. Create and Workspace
/// open as pushed routes so playback pauses when leaving a preview.
class RootShell extends StatefulWidget {
  const RootShell({super.key});

  @override
  State<RootShell> createState() => _RootShellState();
}

class _RootShellState extends State<RootShell> {
  final ProjectStore _store = const ProjectStore();
  int _index = 0;
  int _homeRefresh = 0;

  void _openCreate() {
    Navigator.of(
      context,
    ).push(MaterialPageRoute<void>(builder: (_) => const CreateScreen()));
  }

  void _openProject(Project project) {
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
    Navigator.of(context).push(
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
          projectStore: _store,
          project: project,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: IndexedStack(
        index: _index,
        children: <Widget>[
          HomeScreen(
            onCreateVideo: _openCreate,
            store: _store,
            onOpenProject: _openProject,
            refreshToken: _homeRefresh,
          ),
          ProjectsScreen(onCreateVideo: _openCreate, store: _store),
          const StatusScreen(),
        ],
      ),
      bottomNavigationBar: BottomNavigationBar(
        currentIndex: _index,
        onTap: (int index) => setState(() {
          _index = index;
          if (index == 0) {
            _homeRefresh++;
          }
        }),
        items: const <BottomNavigationBarItem>[
          BottomNavigationBarItem(
            icon: Icon(Icons.home_outlined),
            activeIcon: Icon(Icons.home),
            label: 'Home',
          ),
          BottomNavigationBarItem(
            icon: Icon(Icons.folder_outlined),
            activeIcon: Icon(Icons.folder),
            label: 'Projects',
          ),
          BottomNavigationBarItem(
            icon: Icon(Icons.insights_outlined),
            activeIcon: Icon(Icons.insights),
            label: 'Status',
          ),
        ],
      ),
    );
  }
}
