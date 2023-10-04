from __future__ import print_function
from __future__ import absolute_import

from m5.params import *
from m5.objects import *

from common import FileSystemConfig

from topologies.BaseTopology import SimpleTopology

import math
import sys

class Mesh_IO_Center(SimpleTopology):
    description = 'Mesh_IO_Center for Chiplets, like AMD ZEN 3'

    def __init__(self, controllers):
        self.nodes = controllers

    def makeTopology(self, options, network, IntLink, ExtLink, Router):
        nodes = self.nodes
        
        self.link_latency = options.link_latency
        router_latency = options.router_latency 
        print(f'Base Link Latency: {self.link_latency}')
        self.chiplet_link_latency = options.link_latency + options.chiplet_latency_increase
        print(f'Chiplet Link Latency: {self.chiplet_link_latency}')
        cache_nodes = []
        dir_nodes = []
        dma_nodes = []
        for node in nodes:
            if node.type == 'L1Cache_Controller' or \
                    node.type == 'L2Cache_Controller' or \
                    node.type == 'L0Cache_Controller':
                cache_nodes.append(node)
            elif node.type == 'Directory_Controller':
                dir_nodes.append(node)
            elif node.type == 'DMA_Controller':
                dma_nodes.append(node)
            else:
                print('Unkown node controller {t}'.format(t=node.type))
                assert (False)
        print(f'Number of Each Level Cache Controller: {len(cache_nodes)/3}')
        print(f'Number of DMA Controllers: {len(dma_nodes)}')
        print(f'Number of Directory Controllers: {len(dir_nodes)}')
        
        num_dir_nodes = len(dir_nodes)
        num_cpu_chiplets = num_dir_nodes
        num_cpus_per_chiplet = int(options.num_cpus / num_cpu_chiplets)

        print(f'Number of CPUs per Chiplet: {num_cpus_per_chiplet}')
        
        self.num_rows = int(math.sqrt(num_cpus_per_chiplet))  # 4 for 16 cpus
        self.num_cols = int(num_cpus_per_chiplet / self.num_rows)  # 4 for 16 cpus

        print(f'Total Number of CPUs in Chiplets: {self.num_rows * self.num_cols * num_cpu_chiplets}')
        assert ((self.num_rows * self.num_cols * num_cpu_chiplets) == options.num_cpus)  # all is well

        self.num_io_chiplet_rows = int(math.sqrt(num_dir_nodes))
        self.num_io_chiplet_cols = int(num_dir_nodes / self.num_io_chiplet_rows)  # 2 x 2 for 4 memory controllers

        print(f'Total Number of Directory Controllers in I/O Chiplet: {self.num_io_chiplet_cols * self.num_io_chiplet_rows}')
        assert ((self.num_io_chiplet_cols * self.num_io_chiplet_rows) == num_dir_nodes)  # all is still well

        # the number of caches must be a
        # multiple of the number of cpus and the number of directories
        # must be power of 2
        caches_per_cpu_router, remainder = divmod(len(cache_nodes), options.num_cpus)
        assert (remainder == 0)
        assert(num_dir_nodes % 2 == 0)

        self.num_routers = options.num_cpus + num_dir_nodes # 68 for 64 and 4
        print(f'Total Number of Routers for all Chiplets: {self.num_routers}')
        
        # Create the routers in the mesh
        routers = [Router(router_id=i, latency=router_latency) \
                   for i in range(self.num_routers)]
        network.routers = routers # will reference the same list

        num_cpus = options.num_cpus

        assert (num_cpus == self.num_routers - num_dir_nodes)

        # link counter to set unique link ids
        self.link_count = 0

        # Connect each cache controller to the appropriate router
        ext_links = []
        for (i, n) in enumerate(cache_nodes): # 0 1 2 3 4 5
            cntrl_level, router_id = divmod(i, self.num_routers - num_dir_nodes)  # every cpu now gets a cache and router, only the first CPU # of routers
            assert (cntrl_level < caches_per_cpu_router)
            n.router_id = router_id
            ext_links.append(ExtLink(link_id=self.link_count, ext_node=n,
                                     int_node=routers[router_id],
                                     latency=self.link_latency))
            print(f'[Topology] Connect {n.type} {n.version} to Rounter {router_id} with Link Latency {self.link_latency}')
            self.link_count += 1

        # Connect the dma nodes to router 0.  These should only be DMA nodes.
        for (i, node) in enumerate(dma_nodes):
            assert (node.type == 'DMA_Controller')
            node.router_id = 0
            ext_links.append(ExtLink(link_id=self.link_count, ext_node=node,
                                     int_node=routers[0],
                                     latency=self.link_latency))
            # don't care where DMA nodes go, will not be using this feature

        # perfect up until this point

        r_id = self.num_routers - num_dir_nodes  # start point for i/o chiplet
        for (i, node) in enumerate(dir_nodes):
            print(f'[I/O Chiplet] Directory Controller {i} -> Router {r_id} with Link Latency {self.link_latency}')
            dir_nodes[i].router_id = r_id
            ext_links.append(ExtLink(link_id=self.link_count, ext_node=dir_nodes[i],
                                     int_node=routers[r_id], latency=self.link_latency))
            r_id += 1
            self.link_count += 1

        # Create the mesh links.
        int_links = []

        io_chiplet_router_start = self.num_routers - num_dir_nodes # start point for i/o chiplet

        for row in range(self.num_io_chiplet_rows):
            for col in range(self.num_io_chiplet_cols):
                if col + 1 < self.num_io_chiplet_cols:  # if next col is within range
                    east_out = io_chiplet_router_start + col + (row * self.num_io_chiplet_cols)
                    west_in = io_chiplet_router_start + (col + 1) + (row * self.num_io_chiplet_cols)
                    int_links.append(IntLink(link_id=self.link_count, # connect to next router
                                             src_node=routers[east_out],
                                             dst_node=routers[west_in],
                                             src_outport="East",
                                             dst_inport="West",
                                             latency=self.link_latency,
                                             weight=1))
                    print(f'[I/O Chiplet] Router {east_out} -> Router {west_in} with Link Latency {self.link_latency}')
                    self.link_count += 1

        # West output to East input links (weight = 1)

        for row in range(self.num_io_chiplet_rows):
            for col in range(self.num_io_chiplet_cols):
                if col + 1 < self.num_io_chiplet_cols:
                    east_in = io_chiplet_router_start + col + (row * self.num_io_chiplet_cols)
                    west_out = io_chiplet_router_start + (col + 1) + (row * self.num_io_chiplet_cols)
                    int_links.append(IntLink(link_id=self.link_count,
                                             src_node=routers[west_out],
                                             dst_node=routers[east_in],
                                             src_outport="West",
                                             dst_inport="East",
                                             latency=self.link_latency,
                                             weight=1))
                    print(f'[I/O Chiplet] Router {west_out} -> Router {east_in} with Link Latency {self.link_latency}')
                    self.link_count += 1

        # North output to South input links (weight = 1)

        for col in range(self.num_io_chiplet_cols):
            for row in range(self.num_io_chiplet_rows):
                if row + 1 < self.num_io_chiplet_rows:
                    north_out = io_chiplet_router_start + col +  (row * self.num_io_chiplet_cols)
                    south_in = io_chiplet_router_start + col + ((row + 1) * self.num_io_chiplet_cols)
                    int_links.append(IntLink(link_id=self.link_count,
                                             src_node=routers[north_out],
                                             dst_node=routers[south_in],
                                             src_outport="North",
                                             dst_inport="South",
                                             latency=self.link_latency,
                                             weight=1))
                    print(f'[I/O Chiplet] Router {north_out} -> Router {south_in} with Link Latency {self.link_latency}')
                    self.link_count += 1

        # South output to North input links (weight = 1)

        for col in range(self.num_io_chiplet_cols):
            for row in range(self.num_io_chiplet_rows):
                if row + 1 < self.num_io_chiplet_rows:
                    north_in = io_chiplet_router_start + col + (row * self.num_io_chiplet_cols)
                    south_out = io_chiplet_router_start + col + ((row + 1) * self.num_io_chiplet_cols)
                    int_links.append(IntLink(link_id=self.link_count,
                                             src_node=routers[south_out],
                                             dst_node=routers[north_in],
                                             src_outport="South",
                                             dst_inport="North",
                                             latency=self.link_latency,
                                             weight=1))
                    print(f'[I/O Chiplet] Router {south_out} -> Router {north_in} with Link Latency {self.link_latency}')
                    self.link_count += 1

        # print config

        print('Configuration:\n Number of CPU Chiplets ' + str(num_cpu_chiplets) +
              '\nCPU Chiplet config: ' + str(self.num_rows) + ' x ' + str(self.num_cols) +
              '\nI/O Chiplet Config: ' + str(self.num_io_chiplet_rows) + ' x ' + str(self.num_io_chiplet_cols)
              + '\n Mesh')

        for dir_idx in range(len(dir_nodes)):
            dir_nodes[dir_idx].numa_banks = []  # leave empty for now, not useful once we do 1:(num_chiplets_llc) address mapping

        network.ext_links = ext_links

        # Smaller weight means higher priority 
        weightX = 1
        weightY = 2
        if options.routing_YX:
            print('XY Routing Selected')
            weightX = 2
            weightY = 1
        else:
            print('No XY Routing')

        test_num_cpus = 0
        print('Num CPU Chiplets', num_cpu_chiplets)
        for chiplet in range(num_cpu_chiplets):
            print(chiplet)
            print('Topology for CPU Chiplet ' + str(chiplet) + ': ')

            # East output to West input links (weight = 1)
            # s.t east_out = self router id and west_in = next router id, pattern continue

            for row in range(self.num_rows):
                for col in range(self.num_cols):
                    test_num_cpus += 1
                    if col + 1 < self.num_cols:  # if next col is within range
                        east_out = (chiplet * num_cpus_per_chiplet) + col + (row * self.num_cols)
                        west_in = (chiplet * num_cpus_per_chiplet) + (col + 1) + (row * self.num_cols)
                        int_links.append(IntLink(link_id=self.link_count,
                                                 src_node=routers[east_out],
                                                 dst_node=routers[west_in],
                                                 src_outport="East",
                                                 dst_inport="West",
                                                 latency=self.link_latency,
                                                 weight=weightX))
                        print(f'[CPU Chiplet] Router {east_out} -> Router {west_in} with Link Latency{self.link_latency}')
                        self.link_count += 1

            # West output to East input links (weight = 1)

            for row in range(self.num_rows):
                for col in range(self.num_cols):
                    if col + 1 < self.num_cols:
                        east_in = (chiplet * num_cpus_per_chiplet) + col + (row * self.num_cols)
                        west_out = (chiplet * num_cpus_per_chiplet) + (col + 1) + (row * self.num_cols)
                        int_links.append(IntLink(link_id=self.link_count,
                                                 src_node=routers[west_out],
                                                 dst_node=routers[east_in],
                                                 src_outport="West",
                                                 dst_inport="East",
                                                 latency=self.link_latency,
                                                 weight=weightX))
                        print(f'[CPU Chiplet] Router {west_out} -> Router {east_in} with Link Latency{self.link_latency}')
                        self.link_count += 1

            # North output to South input links (weight = 2)

            for col in range(self.num_cols):
                for row in range(self.num_rows):
                    if row + 1 < self.num_rows:
                        north_out = (chiplet * num_cpus_per_chiplet) + col + (row * self.num_cols)
                        south_in = (chiplet * num_cpus_per_chiplet) + col + ((row + 1) * self.num_cols)
                        int_links.append(IntLink(link_id=self.link_count,
                                                 src_node=routers[north_out],
                                                 dst_node=routers[south_in],
                                                 src_outport="North",
                                                 dst_inport="South",
                                                 latency=self.link_latency,
                                                 weight=weightY))
                        print(f'[CPU Chiplet] Router {north_out} -> Router {south_in} with Link Latency{self.link_latency}')
                        self.link_count += 1

            # South output to North input links (weight = 2)

            for col in range(self.num_cols):
                for row in range(self.num_rows):
                    if row + 1 < self.num_rows:
                        north_in = (chiplet * num_cpus_per_chiplet) + col + (row * self.num_cols)
                        south_out = (chiplet * num_cpus_per_chiplet) + col + ((row + 1) * self.num_cols)
                        int_links.append(IntLink(link_id=self.link_count,
                                                 src_node=routers[south_out],
                                                 dst_node=routers[north_in],
                                                 src_outport="South",
                                                 dst_inport="North",
                                                 latency=self.link_latency,
                                                 weight=weightY))
                        print(f'[CPU Chiplet] Router {south_out} -> Router {north_in} with Link Latency{self.link_latency}')
                        self.link_count += 1

        # everything is hooked up

        assert (test_num_cpus == num_cpus)

        
        # connect I/O chiplet to CPU chiplets
        # have each router on the I/O chiplet take an equal load of CPU chiplets

        cpu_chiplets_per_io_router = int(num_cpu_chiplets / num_dir_nodes)  # 1 per router in this case

        chiplets_connected = 0  # determine which router connecting (not correspond to router_id)
        for io_router_id in range(self.num_routers - num_dir_nodes, self.num_routers):
            for cpu_chiplet in range(cpu_chiplets_per_io_router):
                cpu_router_id = (chiplets_connected * num_cpus_per_chiplet)
                int_links.append(IntLink(link_id=self.link_count,
                                         src_node=routers[io_router_id],
                                         dst_node=routers[cpu_router_id],
                                         latency=self.chiplet_link_latency,
                                         weight=1))  # indeterminate weight
                self.link_count += 1
                int_links.append(IntLink(link_id=self.link_count,
                                         src_node=routers[cpu_router_id],
                                         dst_node=routers[io_router_id],
                                         latency=self.chiplet_link_latency,
                                         weight=1))
                self.link_count += 1
                print(f'[I/O to CPU Chiplet] Router {io_router_id} <-> Router {cpu_router_id} with Link Latency {self.chiplet_link_latency}')
                chiplets_connected += 1

        assert (chiplets_connected == num_cpu_chiplets)

        network.int_links = int_links

        print('flushing stdout')
        sys.stdout.flush()


# Register nodes with filesystem
def registerTopology(self, options):
    if options.no_file_system:
        return
    i = 0
    for n in self.numa_nodes:
        if n:
            FileSystemConfig.register_node(n,
                                           MemorySize(options.mem_size) // self.num_numa_nodes, i)
        i += 1
