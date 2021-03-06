"""This code was imported from tbmoon's 'facenet' repository:
    https://github.com/tbmoon/facenet/blob/master/data_loader.py

    The code was modified to support multiprocessing during the triplet generation process to speed up the generation
     process and to support .png, .jpg, and .jpeg files.
"""


import os
import numpy as np
import pandas as pd
import torch
import glob
import multiprocessing  # For generating triplets using separate python processes because of the GIL limitation for a Python interpreter
from tqdm import tqdm
from PIL import Image
from torch.utils.data import Dataset


class TripletFaceDataset(Dataset):
    def __init__(self, root_dir, csv_name, num_triplets, num_generate_triplets_processes=0, training_triplets_path=None,
                 transform=None):
        """Args:

        root_dir: Absolute path to dataset.
        csv_name: Path to csv file containing the image paths inside root_dir.
        num_triplets: Number of triplets required to be generated.
        num_generate_triplets_processes: Number of separate Python processes to be created for the triplet generation
                                          process. A value of 0 would generate a number of processes equal to the
                                          number of available CPU cores.
        training_triplets_path: Path to a pre-generated triplet numpy file to skip the triplet generation process.
        transform: Required image transformation (augmentation) settings.
        """

        # Modified here to set the data types of the dataframe columns to be suitable for other datasets other than the
        #   VggFace2 dataset (Casia-WebFace in this case because of the identities starting with numbers automatically
        #   forcing the 'name' column as being of type 'int' instead of type 'object')
        self.df = pd.read_csv(csv_name, dtype={'id': object, 'name': object, 'class': int})
        self.root_dir = root_dir
        self.num_triplets = num_triplets
        self.transform = transform

        if num_generate_triplets_processes == 0:
            self.num_generate_triplets_processes = os.cpu_count()
        else:
            self.num_generate_triplets_processes = num_generate_triplets_processes

        if training_triplets_path is None:
            self.training_triplets = self.generate_triplets(
                df=self.df,
                num_triplets=self.num_triplets,
                num_generate_triplets_processes=self.num_generate_triplets_processes
            )
        else:
            self.training_triplets = np.load(training_triplets_path)

    def make_dictionary_for_face_class(self, df):
        """
          - face_classes = {'class0': [class0_id0, ...], 'class1': [class1_id0, ...], ...}
        """
        face_classes = dict()
        for idx, label in enumerate(df['class']):
            if label not in face_classes:
                face_classes[label] = []
            face_classes[label].append(df.iloc[idx, 0])

        return face_classes

    def _generate_triplets(self, df, classes, face_classes, num_triplets, process_id):
        # Initialize new random state for each process to avoid repeating the same random generations by each process
        #  Thanks to YoonSeongGyeol https://github.com/tamerthamoqa/facenet-pytorch-vggface2/issues/4#issue-730097818
        randomstate = np.random.RandomState(seed=None)
        
        triplets = []
        progress_bar = tqdm(range(int(num_triplets)))  # tqdm progress bar does not iterate through float numbers

        for _ in progress_bar:

            """
              - randomly choose anchor, positive and negative images for triplet loss
              - anchor and positive images in pos_class
              - negative image in neg_class
              - at least, two images needed for anchor and positive images in pos_class
              - negative image should have different class as anchor and positive images by definition
            """

            pos_class = randomstate.choice(classes)
            neg_class = randomstate.choice(classes)

            while len(face_classes[pos_class]) < 2:
                pos_class = randomstate.choice(classes)

            while pos_class == neg_class:
                neg_class = randomstate.choice(classes)

            pos_name = df.loc[df['class'] == pos_class, 'name'].values[0]
            neg_name = df.loc[df['class'] == neg_class, 'name'].values[0]

            if len(face_classes[pos_class]) == 2:
                ianc, ipos = randomstate.choice(2, size=2, replace=False)

            else:
                ianc = randomstate.randint(0, len(face_classes[pos_class]))
                ipos = randomstate.randint(0, len(face_classes[pos_class]))

                while ianc == ipos:
                    ipos = randomstate.randint(0, len(face_classes[pos_class]))

            ineg = randomstate.randint(0, len(face_classes[neg_class]))

            triplets.append(
                [
                    face_classes[pos_class][ianc],
                    face_classes[pos_class][ipos],
                    face_classes[neg_class][ineg],
                    pos_class,
                    neg_class,
                    pos_name,
                    neg_name
                ]
            )

        np.save('datasets/temp/temp_training_triplets_{}.npy'.format(process_id), triplets)

    def generate_triplets(self, df, num_triplets, num_generate_triplets_processes):
        total_triplets = []
        classes = df['class'].unique()
        face_classes = self.make_dictionary_for_face_class(df)

        print("\nGenerating {} triplets using {} Python processes ...".format(
                num_triplets,
                num_generate_triplets_processes
            )
        )

        # If True, there are residual number of triplets to be generated after the processes are done
        flag_residual_triplets = False
        triplet_residual = num_triplets % num_generate_triplets_processes

        if triplet_residual == 0:
            num_triplets_per_process = num_triplets / num_generate_triplets_processes
        else:
            flag_residual_triplets = True
            num_triplets_per_process = num_triplets - triplet_residual
            num_triplets_per_process = num_triplets_per_process / num_generate_triplets_processes

        processes = []
        for i in range(num_generate_triplets_processes):
            processes.append(multiprocessing.Process(
                    target=self._generate_triplets,
                    args=(df, classes, face_classes, num_triplets_per_process, i)
                )
            )

        for process in processes:
            process.start()

        for process in processes:
            process.join()  # Block execution until all spawned processes are done

        if flag_residual_triplets:
            print("Processes are done. Residual number of tripelts {} detected and are being generated by main process ...".format(triplet_residual))
            self._generate_triplets(
                df=df,
                classes=classes,
                face_classes=face_classes,
                num_triplets=triplet_residual,
                process_id=num_generate_triplets_processes + 1
            )

        numpy_files = glob.glob("datasets/temp/*.npy")
        for numpy_file in numpy_files:
            total_triplets.append(np.load(numpy_file))   # numpy file already contains "datasets/temp" in its path
            os.remove(numpy_file)

        # Convert total triplets list from 3D shape to 2D shape
        total_triplets = [elem for list in total_triplets for elem in list]

        print("Saving training triplets list in datasets/ directory ...")
        np.save('datasets/training_triplets_{}.npy'.format(num_triplets), total_triplets)
        print("Training triplets' list Saved!\n")

        return total_triplets

    # Added this method to allow .jpg, .png, and .jpeg image support
    def add_extension(self, path):
        if os.path.exists(path + '.jpg'):
            return path + '.jpg'
        elif os.path.exists(path + '.png'):
            return path + '.png'
        elif os.path.exists(path + '.jpeg'):
            return path + '.jpeg'
        else:
            raise RuntimeError('No file "{}" with extension png or jpg or jpeg.'.format(path))

    def __getitem__(self, idx):

        anc_id, pos_id, neg_id, pos_class, neg_class, pos_name, neg_name = self.training_triplets[idx]

        anc_img = self.add_extension(os.path.join(self.root_dir, str(pos_name), str(anc_id)))
        pos_img = self.add_extension(os.path.join(self.root_dir, str(pos_name), str(pos_id)))
        neg_img = self.add_extension(os.path.join(self.root_dir, str(neg_name), str(neg_id)))

        # Modified to open as PIL image in the first place
        anc_img = Image.open(anc_img)
        pos_img = Image.open(pos_img)
        neg_img = Image.open(neg_img)

        pos_class = torch.from_numpy(np.array([pos_class]).astype('long'))
        neg_class = torch.from_numpy(np.array([neg_class]).astype('long'))

        sample = {
            'anc_img': anc_img,
            'pos_img': pos_img,
            'neg_img': neg_img,
            'pos_class': pos_class,
            'neg_class': neg_class
        }

        if self.transform:
            sample['anc_img'] = self.transform(sample['anc_img'])
            sample['pos_img'] = self.transform(sample['pos_img'])
            sample['neg_img'] = self.transform(sample['neg_img'])

        return sample

    def __len__(self):
        return len(self.training_triplets)
