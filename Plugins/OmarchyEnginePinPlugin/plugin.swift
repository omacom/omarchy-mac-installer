import Foundation
import PackagePlugin

@main
struct OmarchyEnginePinPlugin: BuildToolPlugin {
  func createBuildCommands(context: PluginContext, target: any Target) throws -> [Command] {
    let lock = context.package.directoryURL
      .appending(components: "Engine", "source-lock.json")
    let output = context.pluginWorkDirectoryURL
      .appending(component: "ValidationEnginePin.swift")
    return [
      .buildCommand(
        displayName: "Generating the engine pin from Engine/source-lock.json",
        executable: try context.tool(named: "OmarchyEnginePinGenerator").url,
        arguments: [
          lock.path(percentEncoded: false),
          output.path(percentEncoded: false),
        ],
        inputFiles: [lock],
        outputFiles: [output]
      )
    ]
  }
}
