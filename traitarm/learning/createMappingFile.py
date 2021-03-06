def map(gemd,speciesFile,  out):
    "bergeys_data/datasetSpeciesRefSeq.txt"
    "bergeys_data/genome_metadata"
    "bergeys_data/map_v2.txt"
    #refseqs = {}
    refseqs = []
    id2faa = {}
    op = []
    
    # parse input
    with open(speciesFile) as file:
        for line in file:
            tmp = line.replace("\n", "").split("\t")
            refseqs.extend([el.strip() for el in tmp[1].split(",")])
           # refseqs[tmp[0]] = [el.strip() for el in tmp[1].split(",")]
            #print refseqs
    refseqs = set(refseqs)
    
    # create a dictionary
    with open(gemd) as file:
        for line in file:
            tmp = line.split("\t")
            id2faa[tmp[3]] = [tmp[0], tmp[1], tmp[19]]
    
    mis = 0
    with open(out, "w") as file:
        file.write("sample_file_name\tsample_name\n")
        for sp in refseqs:
     #       try:
      #          sp_name = id2faa[sp][1]
       #     except KeyError:
        #        mis += 1
         #       print "Main species not found: ", sp, "Some lines skipped"
          #      continue
    
           # for rs in refseqs[sp]:
                #try:
                 #   tmp = id2faa[rs]
                  #  file.write(tmp[0] + ".RefSeq.faa\t" + sp_name + "\n")
                #except KeyError:
                 #   mis += 1
            try:
                tmp = id2faa[sp]
                if tmp[2] != "-": 
                    file.write(tmp[0] + ".RefSeq.faa\t" + sp + "\n")
                else:
                    file.write(tmp[0] + ".PATRIC.faa\t" + sp + "\n")
            except KeyError:
                mis += 1

    print "not found in file: ", mis
if __name__=="__main__":
    import argparse
    parser = argparse.ArgumentParser("create an annotation sample table")
    parser.add_argument("patric_f", help='patric metadata file')
    parser.add_argument("mapping_f", help='strain id mapping')
    parser.add_argument("out_f", help='out sample table')
    args = parser.parse_args()
    map(args.patric_f, args.mapping_f, args.out_f)
