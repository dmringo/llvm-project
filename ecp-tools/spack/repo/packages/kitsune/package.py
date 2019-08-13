# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from spack import *


class Kitsune(CMakePackage):
    """Kitsune is a fork of LLVM that enables optimization within on-node parallel
    constructs by replacing opaque runtime function calls with true parallel
    entities at the LLVM IR level.
    """

    homepage = 'https://github.com/lanl/kitsune'
    url = 'https://github.com/lanl/kitsune/archive/kitsune-0.8.0.tar.gz'


    family = 'compiler'  # Used by lmod


    # NOTE: The debug version of LLVM is an order of magnitude larger than
    # the release version, and may take up 20-30 GB of space. If you want
    # to save space, build with `build_type=Release`.

    variant('clang', default=True,
            description="Build the LLVM C/C++/Objective-C compiler frontend")

    # TODO: The current version of this package unconditionally disables CUDA.
    #       Better would be to add a "cuda" variant that:
    #        - Adds dependency on the "cuda" package when enabled
    #        - Sets the necessary CMake flags when enabled
    #        - Disables CUDA (as this current version does) only when the
    #          variant is also disabled.

    # variant('cuda', default=False,
    #         description="Build the LLVM with CUDA features enabled")

    variant('lldb', default=True, description="Build the LLVM debugger")
    variant('lld', default=True, description="Build the LLVM linker")
    variant('internal_unwind', default=True,
            description="Build the libcxxabi libunwind")
    variant('polly', default=True,
            description="Build the LLVM polyhedral optimization plugin, "
            "only builds for 3.7.0+")
    variant('libcxx', default=True,
            description="Build the LLVM C++ standard library")
    variant('compiler-rt', default=True,
            description="Build LLVM compiler runtime, including sanitizers")
    variant('gold', default=True,
            description="Add support for LTO with the gold linker plugin")
    variant('shared_libs', default=False,
            description="Build all components as shared libraries, faster, "
            "less memory to build, less stable")
    variant('link_dylib', default=False,
            description="Build and link the libLLVM shared library rather "
            "than static")
    variant('all_targets', default=False,
            description="Build all supported targets, default targets "
            "<current arch>,NVPTX,AMDGPU,CppBackend")
    variant('build_type', default='Release',
            description='CMake build type',
            values=('Debug', 'Release', 'RelWithDebInfo', 'MinSizeRel'))
    variant('python', default=False, description="Install python bindings")
    extends('python', when='+python')

    # Build dependency
    depends_on('cmake@3.4.3:', type='build')
    depends_on('python', when='~python', type='build')

    # Universal dependency
    depends_on('python', when='+python')

    # openmp dependencies
    depends_on('perl-data-dumper', type=('build'))

    # lldb dependencies
    depends_on('ncurses', when='+lldb')
    depends_on('swig', when='+lldb')
    depends_on('libedit', when='+lldb')
    depends_on('py-six', when='@5.0.0: +lldb +python')

    # gold support
    depends_on('binutils+gold', when='+gold')

    # polly plugin
    depends_on('gmp', when='@:3.6.999 +polly')
    depends_on('isl', when='@:3.6.999 +polly')

    base_url = 'http://llvm.org/releases/%%(version)s/%(pkg)s-%%(version)s.src.tar.xz'
    llvm_url = base_url % {'pkg': 'llvm'}
    # Flang uses its own fork of llvm.
    flang_llvm_url = 'https://github.com/flang-compiler/llvm.git'
    kitsune_url = 'https://github.com/lanl/kitsune.git'


    kitsune_releases = [
        {
            'version': 'develop',
            'branch': 'release/8.x',
            'numeric-equiv': '8'
        }
    ]


    for release in releases:
        if release['version'] == 'develop':
            version(release['version'], svn=release['repo'])

            for rname, repo in release['resources'].items():
                resource(name=rname,
                         svn=repo,
                         destination=resources[rname]['destination'],
                         when='@%s%s' % (release['version'],
                                         resources[rname].get('variant', "")),
                         placement=resources[rname].get('placement', None))
        else:
            version(release['version'], release['md5'], url=llvm_url % release)

            for rname, md5 in release['resources'].items():
                resource(name=rname,
                         url=resources[rname]['url'] % release,
                         md5=md5,
                         destination=resources[rname]['destination'],
                         when='@%s%s' % (release['version'],
                                         resources[rname].get('variant', "")),
                         placement=resources[rname].get('placement', None))

    for release in flang_releases:
        if release['version'] == 'develop':
            version('flang-' + release['version'], git=flang_llvm_url, branch=release['branch'])

            for rname, branch in release['resources'].items():
                flang_resource = flang_resources[rname]
                resource(name=rname,
                         git=flang_resource['git'],
                         branch=branch,
                         destination=flang_resource['destination'],
                         placement=flang_resource['placement'],
                         when='@flang-' + release['version'])

        else:
            version('flang-' + release['version'], git=flang_llvm_url, commit=release['commit'])

            for rname, commit in release['resources'].items():
                flang_resource = flang_resources[rname]
                resource(name=rname,
                         git=flang_resource['git'],
                         commit=commit,
                         destination=flang_resource['destination'],
                         placement=flang_resource['placement'],
                         when='@flang-' + release['version'])


    for release in kitsune_releases:
        version(release['numeric-equiv'] + '-kitsune-' + release['version'],
                git=kitsune_url, branch=release['branch'])


    conflicts('+clang_extra', when='~clang')
    conflicts('+lldb',        when='~clang')

    # LLVM 4 and 5 does not build with GCC 8
    conflicts('%gcc@8:',       when='@0:5')
    conflicts('%gcc@:5.0.999', when='@8:')

    # Github issue #4986
    patch('llvm_gcc7.patch', when='@4.0.0:4.0.1+lldb %gcc@7.0:')

    @run_before('cmake')
    def check_darwin_lldb_codesign_requirement(self):
        if not self.spec.satisfies('+lldb platform=darwin'):
            return
        codesign = which('codesign')
        mkdir('tmp')
        llvm_check_file = join_path('tmp', 'llvm_check')
        copy('/usr/bin/false', llvm_check_file)

        try:
            codesign('-f', '-s', 'lldb_codesign', '--dryrun',
                     llvm_check_file)

        except ProcessError:
            explanation = ('The "lldb_codesign" identity must be available'
                           ' to build LLVM with LLDB. See https://llvm.org/'
                           'svn/llvm-project/lldb/trunk/docs/code-signing'
                           '.txt for details on how to create this identity.')
            raise RuntimeError(explanation)

    def setup_environment(self, spack_env, run_env):
        spack_env.append_flags('CXXFLAGS', self.compiler.cxx11_flag)

        if '+clang' in self.spec:
            run_env.set('CC', join_path(self.spec.prefix.bin, 'clang'))
            run_env.set('CXX', join_path(self.spec.prefix.bin, 'clang++'))

    # With the new LLVM monorepo, CMakeLists.txt lives in the llvm subdirectory.
    @property
    def root_cmakelists_dir(self):
        """The relative path to the directory containing CMakeLists.txt

        This path is relative to the root of the extracted tarball,
        not to the ``build_directory``. Defaults to the current directory.

        :return: directory containing CMakeLists.txt
        """
        return 'llvm'



    def cmake_args(self):
        spec = self.spec
        cmake_args = [
            '-DLLVM_REQUIRES_RTTI:BOOL=ON',
            '-DLLVM_ENABLE_RTTI:BOOL=ON',
            '-DLLVM_ENABLE_EH:BOOL=ON',
            '-DCLANG_DEFAULT_OPENMP_RUNTIME:STRING=libomp',
            '-DPYTHON_EXECUTABLE:PATH={0}'.format(spec['python'].command.path),
        ]

        # TODO: Instead of unconditionally disabling CUDA, add a "cuda" variant
        #       (see TODO above), and set the paths if enabled.
        cmake_args.extend([
            '-DCUDA_TOOLKIT_ROOT_DIR:PATH=IGNORE',
            '-DCUDA_SDK_ROOT_DIR:PATH=IGNORE',
            '-DCUDA_NVCC_EXECUTABLE:FILEPATH=IGNORE',
            '-DLIBOMPTARGET_DEP_CUDA_DRIVER_LIBRARIES:STRING=IGNORE'])

        if '+gold' in spec:
            cmake_args.append('-DLLVM_BINUTILS_INCDIR=' +
                              spec['binutils'].prefix.include)
        if '+polly' in spec:
            cmake_args.append('-DLINK_POLLY_INTO_TOOLS:Bool=ON')
        else:
            cmake_args.extend(['-DLLVM_EXTERNAL_POLLY_BUILD:Bool=OFF',
                               '-DLLVM_TOOL_POLLY_BUILD:Bool=OFF',
                               '-DLLVM_POLLY_BUILD:Bool=OFF',
                               '-DLLVM_POLLY_LINK_INTO_TOOLS:Bool=OFF'])

        if '+python' in spec and '+lldb' in spec and spec.satisfies('@5.0.0:'):
            cmake_args.append('-DLLDB_USE_SYSTEM_SIX:Bool=TRUE')
        if '+clang' not in spec:
            cmake_args.append('-DLLVM_EXTERNAL_CLANG_BUILD:Bool=OFF')
        if '+lldb' not in spec:
            cmake_args.extend(['-DLLVM_EXTERNAL_LLDB_BUILD:Bool=OFF',
                               '-DLLVM_TOOL_LLDB_BUILD:Bool=OFF'])
        if '+lld' not in spec:
            cmake_args.append('-DLLVM_TOOL_LLD_BUILD:Bool=OFF')
        if '+internal_unwind' not in spec:
            cmake_args.append('-DLLVM_EXTERNAL_LIBUNWIND_BUILD:Bool=OFF')
        if '+libcxx' in spec:
            if spec.satisfies('@3.9.0:'):
                cmake_args.append('-DCLANG_DEFAULT_CXX_STDLIB=libc++')
        else:
            cmake_args.append('-DLLVM_EXTERNAL_LIBCXX_BUILD:Bool=OFF')
            cmake_args.append('-DLLVM_EXTERNAL_LIBCXXABI_BUILD:Bool=OFF')
        if '+compiler-rt' not in spec:
            cmake_args.append('-DLLVM_EXTERNAL_COMPILER_RT_BUILD:Bool=OFF')

        if '+shared_libs' in spec:
            cmake_args.append('-DBUILD_SHARED_LIBS:Bool=ON')

        if '+link_dylib' in spec:
            cmake_args.append('-DLLVM_LINK_LLVM_DYLIB:Bool=ON')

        if '+all_targets' not in spec:  # all is default on cmake

            targets = ['NVPTX', 'AMDGPU']
            if spec.version < Version('3.9.0'):
                # Starting in 3.9.0 CppBackend is no longer a target (see
                # LLVM_ALL_TARGETS in llvm's top-level CMakeLists.txt for
                # the complete list of targets)
                targets.append('CppBackend')

            if 'x86' in spec.architecture.target.lower():
                targets.append('X86')
            elif 'arm' in spec.architecture.target.lower():
                targets.append('ARM')
            elif 'aarch64' in spec.architecture.target.lower():
                targets.append('AArch64')
            elif 'sparc' in spec.architecture.target.lower():
                targets.append('Sparc')
            elif ('ppc' in spec.architecture.target.lower() or
                  'power' in spec.architecture.target.lower()):
                targets.append('PowerPC')

            cmake_args.append(
                '-DLLVM_TARGETS_TO_BUILD:STRING=' + ';'.join(targets))

        if spec.satisfies('@4.0.0:') and spec.satisfies('platform=linux'):
            cmake_args.append('-DCMAKE_BUILD_WITH_INSTALL_RPATH=1')
        return cmake_args

    @run_before('build')
    def pre_install(self):
        with working_dir(self.build_directory):
            # When building shared libraries these need to be installed first
            make('install-LLVMTableGen')
            if self.spec.version >= Version('4.0.0'):
                # LLVMDemangle target was added in 4.0.0
                make('install-LLVMDemangle')
            make('install-LLVMSupport')

    @run_after('install')
    def post_install(self):
        if '+clang' in self.spec and '+python' in self.spec:
            install_tree(
                'tools/clang/bindings/python/clang',
                join_path(site_packages_dir, 'clang'))

        with working_dir(self.build_directory):
            install_tree('bin', join_path(self.prefix, 'libexec', 'llvm'))
